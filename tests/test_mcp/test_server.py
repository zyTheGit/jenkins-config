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
    ]
    for name in expected_tools:
        assert name in tool_names


# ============================================================================
# main() 函数测试
# ============================================================================


def test_main_function_exists():
    """验证 main() 函数存在且可调用"""
    from jenkins_config.mcp.server import main

    assert callable(main)


def test_main_calls_register_and_run():
    """验证 main() 调用 _register_tools 和 mcp.run"""
    from jenkins_config.mcp import server

    with patch.object(server, "_register_tools") as mock_register, \
         patch.object(server.mcp, "run") as mock_run:
        server.main()

        mock_register.assert_called_once()
        mock_run.assert_called_once_with(transport="stdio")
