# tests/test_mcp/test_server.py
"""
MCP Server 核心测试

测试覆盖：
- FastMCP 实例创建
- _register_tools() 注册所有 tools
- main() 函数存在且可调用
"""

import asyncio
from unittest.mock import patch


# ============================================================================
# FastMCP 实例测试
# ============================================================================


def test_mcp_instance_created():
    """验证 FastMCP 实例已正确创建"""
    from jenkins_config.mcp.server import mcp
    from mcp.server.fastmcp import FastMCP

    assert mcp is not None
    assert isinstance(mcp, FastMCP)
    assert mcp.name == "jenkins-build"


def test_mcp_instance_is_singleton():
    """验证 mcp 实例在同一进程中是单例"""
    from jenkins_config.mcp.server import mcp as mcp1
    from jenkins_config.mcp.server import mcp as mcp2

    assert mcp1 is mcp2


# ============================================================================
# _register_tools 测试
# ============================================================================


def test_register_tools_imports_all_modules():
    """验证 _register_tools() 成功导入所有 tools 模块"""
    from jenkins_config.mcp.server import _register_tools

    # _register_tools 应该成功执行，不抛出异常
    _register_tools()


def test_register_tools_registers_tools():
    """验证 _register_tools() 后 tools 被注册到 mcp 实例"""
    from jenkins_config.mcp.server import mcp, _register_tools

    _register_tools()

    # 使用 FastMCP 公开接口 list_tools()（异步）获取已注册 tools
    registered_tools = asyncio.run(mcp.list_tools())
    assert len(registered_tools) > 0

    # 验证全部 tools 名称均已注册（含诊断类和 save_config）
    tool_names = [t.name for t in registered_tools]
    expected_tools = [
        # 配置类
        "list_environments",
        "list_projects",
        "show_config",
        "save_config",
        # 历史类
        "show_history",
        "show_history_stats",
        # 诊断类
        "health_check",
        "get_build_status",
        "get_build_log",
        # 构建操作类
        "trigger_build",
        "rebuild_last",
        # 诊断类（阶段①②新增）
        "where_config",
        "doctor",
        # 初始化类（阶段③新增）
        "init_config",
    ]
    for name in expected_tools:
        assert name in tool_names

    # 总数钉住：新增 tool 必须同步登记到本清单，避免注册了却没人知道
    assert len(registered_tools) == 14
    assert sorted(tool_names) == sorted(expected_tools)


def test_register_tools_registers_prompts():
    """验证 3 个 prompt 均已注册，且 setup_workflow 明确引导到 init_config"""
    from jenkins_config.mcp.server import mcp, _register_tools

    _register_tools()

    prompts = asyncio.run(mcp.list_prompts())
    names = [p.name for p in prompts]

    assert len(prompts) == 3
    assert sorted(names) == ["build_workflow", "diagnose_failure", "setup_workflow"]

    from jenkins_config.mcp.prompts import setup_workflow

    text = setup_workflow()
    assert "init_config" in text
    assert "doctor" in text
    assert "where_config" in text
    assert "list_environments" in text


def test_list_tools_generates_output_schema_for_list_tools():
    """验证 list 型 tool 放宽为 list[dict[str, Any]] 后 schema 仍可生成

    FastMCP 会为返回值生成 output schema，容器内类型改宽是有可能被拒的；
    这条用例把"注解能被 FastMCP 接受"钉住，避免只在真实客户端里才暴雷。
    """
    from jenkins_config.mcp.server import mcp, _register_tools

    _register_tools()
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}

    for name in ("list_environments", "list_projects", "show_history"):
        schema = tools[name].outputSchema
        assert schema is not None
        assert schema["properties"]["result"]["type"] == "array"


def test_all_tools_keep_stdout_clean(capsys, monkeypatch, tmp_path):
    """验证调用全部 tool 期间 stdout 始终为空

    stdout 是 JSON-RPC 通道，任何一行 print 都会让客户端解析失败。
    init_config 默认写用户级目录，因此这里把家目录改到 tmp_path，
    避免体检式的全量调用把文件落到开发者真实的 ~/.jenkins-config。
    """
    from pathlib import Path

    from jenkins_config.mcp.server import mcp, _register_tools

    monkeypatch.delenv("JENKINS_MCP_ALLOW_WRITE", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


    def _no_network(*args, **kwargs):
        raise ConnectionError("用例禁止真实外呼")

    # 诊断类 tool 会建客户端；替掉客户端构造即可切断网络，同时保留失败分支
    monkeypatch.setattr(
        "jenkins_config.mcp.utils.build_jenkins_client", _no_network
    )

    _register_tools()
    tools = asyncio.run(mcp.list_tools())
    required_args = {
        "get_build_status": {"job_path": "demo", "build_num": 1},
        "get_build_log": {"job_path": "demo", "build_num": 1},
    }

    capsys.readouterr()
    for tool in tools:
        asyncio.run(mcp.call_tool(tool.name, required_args.get(tool.name, {})))

    assert capsys.readouterr().out == ""


# ============================================================================
# current_log_sinks 测试
# ============================================================================


def test_current_log_sinks_reports_not_installed(monkeypatch):
    """验证未安装 handler 时如实报告，而不是凭 resolve_log_file() 推算文件名"""
    import logging

    from jenkins_config.mcp import server

    monkeypatch.setenv(server.LOG_FILE_ENV_VAR, "auto")
    root = logging.getLogger()
    original_level = root.level
    try:
        server._own_handlers.clear()
        sinks = server.current_log_sinks()

        assert sinks["initialized"] is False
        assert sinks["file_sinks"] == []
        assert "handler 未安装" in sinks["sinks"][0]
    finally:
        _teardown_logging(root, original_level)


def test_current_log_sinks_reports_actual_file(tmp_path, monkeypatch):
    """验证已安装文件 handler 时报出真实落点"""
    import logging

    from jenkins_config.mcp import server

    log_file = tmp_path / "mcp.log"
    monkeypatch.setenv(server.LOG_FILE_ENV_VAR, str(log_file))
    monkeypatch.setenv(server.LOG_LEVEL_ENV_VAR, "INFO")
    root = logging.getLogger()
    original_level = root.level
    try:
        server.setup_logging()
        sinks = server.current_log_sinks()

        assert sinks["initialized"] is True
        assert sinks["level"] == "INFO"
        assert str(log_file) in sinks["file_sinks"][0]
        assert any("stderr" == item for item in sinks["sinks"])
    finally:
        _teardown_logging(root, original_level)


def test_current_log_sinks_degrades_instead_of_raising(monkeypatch):
    """验证探查失败时降级返回，绝不抛出（诊断入口不能把会话打断）"""
    from jenkins_config.mcp import server

    class _Unreadable:
        """遍历即失败的 handler 容器，模拟探查过程中的意外异常"""

        def __iter__(self):
            raise RuntimeError("handler 容器损坏")

    monkeypatch.setattr(server, "_own_handlers", _Unreadable())

    sinks = server.current_log_sinks()

    assert sinks["initialized"] is False
    assert sinks["level"] == "unknown"
    assert "探查失败" in sinks["sinks"][0]
    # 环境变量取值必须照样报出，便于人工核对
    assert server.LOG_FILE_ENV_VAR in sinks["env"]


# ============================================================================
# main() 函数测试
# ============================================================================


def test_main_function_exists():
    """验证 main() 函数存在且可调用"""
    from jenkins_config.mcp.server import main

    assert callable(main)


def test_main_calls_register_and_run():
    """验证 main() 调用 setup_logging、_register_tools 和 mcp.run"""
    from jenkins_config.mcp import server

    with patch.object(server, "setup_logging") as mock_logging, \
         patch.object(server, "_register_tools") as mock_register, \
         patch.object(server.mcp, "run") as mock_run:
        server.main()

        mock_logging.assert_called_once()
        mock_register.assert_called_once()
        mock_run.assert_called_once_with(transport="stdio")


# ============================================================================
# 日志配置测试
# ============================================================================


def test_setup_logging_writes_to_stderr_only(monkeypatch):
    """验证默认只挂 stderr handler——stdout 是 JSON-RPC 通道，写入会破坏协议"""
    import logging
    import sys

    from jenkins_config.mcp import server

    monkeypatch.delenv(server.LOG_FILE_ENV_VAR, raising=False)
    monkeypatch.delenv(server.LOG_LEVEL_ENV_VAR, raising=False)
    root = logging.getLogger()
    original_level = root.level
    try:
        server.setup_logging()
        assert len(server._own_handlers) == 1
        handler = server._own_handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stderr
        assert root.level == logging.WARNING
    finally:
        _teardown_logging(root, original_level)


def test_setup_logging_rejects_unknown_level(monkeypatch):
    """验证非法级别退回 WARNING（getattr 方案会取到 BASIC_FORMAT 之类的字符串并崩溃）"""
    import logging

    from jenkins_config.mcp import server

    monkeypatch.delenv(server.LOG_FILE_ENV_VAR, raising=False)
    monkeypatch.setenv(server.LOG_LEVEL_ENV_VAR, "basic_format")
    root = logging.getLogger()
    original_level = root.level
    try:
        server.setup_logging()
        assert root.level == logging.WARNING
    finally:
        _teardown_logging(root, original_level)


def test_setup_logging_adds_file_handler(tmp_path, monkeypatch):
    """验证 JENKINS_MCP_LOG_FILE 指定路径时追加文件 handler 并真实落盘"""
    import logging

    from jenkins_config.mcp import server

    log_file = tmp_path / "nested" / "mcp.log"
    monkeypatch.setenv(server.LOG_FILE_ENV_VAR, str(log_file))
    monkeypatch.setenv(server.LOG_LEVEL_ENV_VAR, "INFO")
    root = logging.getLogger()
    original_level = root.level
    try:
        server.setup_logging()
        logging.getLogger("jenkins_config.test").info("hello")
        for handler in server._own_handlers:
            handler.flush()

        assert log_file.exists()
        assert "hello" in log_file.read_text(encoding="utf-8")
    finally:
        _teardown_logging(root, original_level)


def _teardown_logging(root, original_level):
    """还原 root logger：只回收 setup_logging 自己装的 handler

    Args:
        root: 根 logger
        original_level: 用例执行前的级别

    Returns:
        None
    """
    from jenkins_config.mcp import server

    for handler in server._own_handlers:
        root.removeHandler(handler)
        handler.close()
    server._own_handlers.clear()
    root.setLevel(original_level)


def test_resolve_log_file_auto_uses_user_log_dir(monkeypatch):
    """验证 JENKINS_MCP_LOG_FILE=auto 落到用户级日志目录，且文件名带进程号"""
    import os

    from jenkins_config.mcp import server
    from jenkins_config.paths import user_log_dir

    monkeypatch.setenv(server.LOG_FILE_ENV_VAR, "auto")
    result = server.resolve_log_file()

    assert result.parent == user_log_dir()
    assert result.name == f"jenkins-config-mcp.{os.getpid()}.log"


def test_resolve_log_file_returns_none_when_unset(monkeypatch):
    """验证未设置 JENKINS_MCP_LOG_FILE 时不写文件日志"""
    from jenkins_config.mcp import server

    monkeypatch.delenv(server.LOG_FILE_ENV_VAR, raising=False)

    assert server.resolve_log_file() is None


def test_prune_pid_logs_keeps_recent_only(tmp_path):
    """验证 auto 模式的 pid 日志按 mtime 只保留最近 N 个，避免无限堆积"""
    import os

    from jenkins_config.mcp import server

    for index in range(6):
        log = tmp_path / f"jenkins-config-mcp.{1000 + index}.log"
        log.write_text("x", encoding="utf-8")
        os.utime(log, (index, index))
    unrelated = tmp_path / "other.log"
    unrelated.write_text("x", encoding="utf-8")

    server._prune_pid_logs(tmp_path, keep=2)

    remaining = sorted(item.name for item in tmp_path.glob("*.log"))
    assert remaining == [
        "jenkins-config-mcp.1004.log",
        "jenkins-config-mcp.1005.log",
        "other.log",
    ]


def test_setup_logging_degrades_when_log_path_unresolvable(monkeypatch, capsys):
    """验证日志路径解析失败时降级为仅 stderr，且提示不被日志级别吞掉

    `~` 展开在 HOME/USERPROFILE 均缺失时抛 RuntimeError（容器 / 服务账号），
    该异常必须被 setup_logging 兜住，否则 Server 直接带栈退出。
    """
    import logging

    from jenkins_config.mcp import server

    monkeypatch.setenv(server.LOG_FILE_ENV_VAR, "auto")
    monkeypatch.setenv(server.LOG_LEVEL_ENV_VAR, "CRITICAL")
    monkeypatch.setattr(
        server, "resolve_log_file", lambda: (_ for _ in ()).throw(RuntimeError("no home"))
    )

    root = logging.getLogger()
    original_level = root.level
    try:
        server.setup_logging()
        assert len(server._own_handlers) == 1
        assert "已降级为仅输出 stderr" in capsys.readouterr().err
    finally:
        _teardown_logging(root, original_level)
