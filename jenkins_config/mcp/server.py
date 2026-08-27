"""
Jenkins MCP Server - 将 Jenkins 自动构建工具暴露为 MCP Tools

使用 FastMCP API 提供标准的 Model Context Protocol 接口，
包括 Tools（构建操作）、Resources（配置数据）和 Prompts（交互模板）。

mcp 依赖采用延迟导入：未安装 mcp extra 时本模块仍可被导入，
仅在 main() 入口处检测依赖并给出友好提示。

日志一律走 stderr：stdout 是 JSON-RPC 通道，写入任何内容都会破坏协议；
需要留存文件时用 JENKINS_MCP_LOG_FILE 指定路径（或设为 auto 落到用户级日志目录）。
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# 日志级别与日志文件的环境变量（文件日志默认关闭，仅输出到 stderr）
LOG_LEVEL_ENV_VAR = "JENKINS_MCP_LOG_LEVEL"
LOG_FILE_ENV_VAR = "JENKINS_MCP_LOG_FILE"

# 允许的日志级别（不用 getattr(logging, name)：那样会命中 BASIC_FORMAT 之类的
# 非级别属性，setLevel 拿到字符串会直接抛 ValueError 把 Server 打崩）
LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# 日志文件名（JENKINS_MCP_LOG_FILE=auto 时置于用户级日志目录）
LOG_FILE_NAME = "jenkins-config-mcp.log"

# auto 模式按 pid 命名，保留最近多少个历史日志文件（含轮转备份）
LOG_FILE_KEEP = 5

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# 延迟初始化的 FastMCP 实例（首次访问时创建）
_mcp_instance: Any = None

# setup_logging 自己安装的 handler，重复调用时只回收这些，不动宿主进程预装的
_own_handlers: list[logging.Handler] = []


class _SafeStreamHandler(logging.StreamHandler):
    """写入失败时静默丢弃的 stderr handler

    不用全局 logging.raiseExceptions = False：那是进程级开关，
    会连带屏蔽宿主进程和第三方库自己 handler 的报错。
    """

    def handleError(self, record: logging.LogRecord) -> None:
        """吞掉本 handler 的写入异常

        Args:
            record: 写入失败的日志记录（忽略）
        """


class _SafeRotatingFileHandler(RotatingFileHandler):
    """写入/轮转失败时静默丢弃的文件 handler

    多个客户端各自拉起一个 MCP Server 进程，指向同一个固定路径时
    轮转会撞车（Windows 上文件被占用，rename 直接失败）。
    这类失败不该打断 JSON-RPC 会话，也不该被 logging 打到 stderr 之外的地方。
    """

    def handleError(self, record: logging.LogRecord) -> None:
        """吞掉本 handler 的写入异常

        Args:
            record: 写入失败的日志记录（忽略）
        """


def _prune_pid_logs(log_dir: Path, keep: int = LOG_FILE_KEEP) -> None:
    """清理用户级日志目录中过旧的 pid 日志文件

    auto 模式的文件名带进程号，Server 每次被客户端拉起都是新进程，
    不清理会在日志目录里无限堆积直到占满磁盘。按 mtime 保留最近 keep 个。

    Args:
        log_dir: 存放 pid 日志的目录
        keep: 保留的文件数量上限

    Example:
        >>> _prune_pid_logs(Path.cwd(), keep=9999)
    """
    stem = Path(LOG_FILE_NAME).stem
    try:
        files = sorted(
            log_dir.glob(f"{stem}.*.log*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            continue


def resolve_log_file() -> Path | None:
    """解析文件日志的落盘路径

    auto 模式的文件名带进程号：每个客户端会各自拉起一个 MCP Server 进程，
    多进程写同一个文件时 RotatingFileHandler 的轮转不可靠（Windows 上
    rename 会因文件被占用而失败）。

    Returns:
        Path 对象；环境变量未设置时返回 None（表示只输出到 stderr）

    Raises:
        RuntimeError: 取值以 ~ 开头且 HOME/USERPROFILE 均缺失（由调用方降级处理）

    Example:
        >>> import os
        >>> os.environ["JENKINS_MCP_LOG_FILE"] = "auto"
        >>> resolve_log_file().suffix
        '.log'
        >>> del os.environ["JENKINS_MCP_LOG_FILE"]
    """
    from jenkins_config.mcp.utils import env_truthy
    from jenkins_config.paths import user_log_dir

    value = os.environ.get(LOG_FILE_ENV_VAR, "").strip()
    if not value:
        return None
    if env_truthy(LOG_FILE_ENV_VAR, ("auto",)):
        stem = Path(LOG_FILE_NAME).stem
        return user_log_dir() / f"{stem}.{os.getpid()}.log"
    return Path(value).expanduser()


def setup_logging() -> None:
    """配置根 logger：stderr 必选，文件按需

    stdout 留给 JSON-RPC，因此 StreamHandler 显式绑定 sys.stderr。
    只由 main() 调用一次；重复调用时先关闭并摘掉上一轮自己装的 handler，
    宿主进程或第三方库预装的 handler 保持不动。
    文件日志的路径解析与 handler 创建都在同一个 try 内：路径解析本身也会失败
    （`~` 展开在无 HOME 的容器里抛 RuntimeError），失败时降级为仅 stderr，
    不影响 Server 启动。降级提示直接写 sys.stderr，避免被 LOG_LEVEL 过滤掉。

    Example:
        >>> setup_logging()  # doctest: +SKIP
    """
    level_name = os.environ.get(LOG_LEVEL_ENV_VAR, "WARNING").strip().upper()
    level = LOG_LEVELS.get(level_name, logging.WARNING)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in _own_handlers:
        root.removeHandler(handler)
        handler.close()
    _own_handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT)

    stderr_handler = _SafeStreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)
    _own_handlers.append(stderr_handler)

    log_file: Path | None = None
    try:
        log_file = resolve_log_file()
        if log_file is None:
            return
        log_file.parent.mkdir(parents=True, exist_ok=True)
        _prune_pid_logs(log_file.parent)
        file_handler = _SafeRotatingFileHandler(
            log_file, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        _own_handlers.append(file_handler)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            f"文件日志不可用（{log_file}），已降级为仅输出 stderr: {exc}",
            file=sys.stderr,
        )


def current_log_sinks() -> dict[str, Any]:
    """只读探查当前已安装的日志落点（绝不重新初始化日志）

    刻意不调用 setup_logging()，也不复用 resolve_log_file() 的 auto 拼名：
    - 调 setup_logging 会在诊断过程中改写宿主进程的 root logger；
    - 用 resolve_log_file() 推算路径，会在 handler 尚未安装时凭空报出一个
      并不存在的文件名，而且无 HOME 时 `~` 展开还会抛 RuntimeError。
    因此这里只遍历 _own_handlers 读取**实际**落点，读不到就如实说"未安装"。

    Returns:
        含 level（当前根 logger 级别名）、sinks（落点描述列表）、
        file_sinks（文件落点的绝对路径列表）、initialized（是否已装过 handler）、
        env（两个日志环境变量的原始取值）的字典；解析失败时 sinks 记为降级说明

    Example:
        >>> sorted(current_log_sinks())
        ['env', 'file_sinks', 'initialized', 'level', 'sinks']
    """
    env = {
        LOG_LEVEL_ENV_VAR: os.environ.get(LOG_LEVEL_ENV_VAR, ""),
        LOG_FILE_ENV_VAR: os.environ.get(LOG_FILE_ENV_VAR, ""),
    }
    try:
        level = logging.getLevelName(logging.getLogger().level)
        sinks: list[str] = []
        file_sinks: list[str] = []
        for handler in _own_handlers:
            base_name = getattr(handler, "baseFilename", "")
            if base_name:
                sinks.append(f"文件: {base_name}")
                file_sinks.append(str(base_name))
            else:
                sinks.append("stderr")
        if not sinks:
            sinks.append("stderr（handler 未安装）")
            return {
                "level": level, "sinks": sinks, "file_sinks": file_sinks,
                "initialized": False, "env": env,
            }
        return {
            "level": level, "sinks": sinks, "file_sinks": file_sinks,
            "initialized": True, "env": env,
        }
    except Exception as exc:  # 诊断入口绝不因自身失败而抛出
        return {
            "level": "unknown",
            "sinks": [f"探查失败: {exc}"],
            "file_sinks": [],
            "initialized": False,
            "env": env,
        }


def get_mcp() -> Any:
    """获取（并惰性创建）FastMCP 实例

    Returns:
        名为 jenkins-build 的 FastMCP 实例（进程内单例）

    Example:
        >>> get_mcp() is get_mcp()  # doctest: +SKIP
        True
    """

    global _mcp_instance
    if _mcp_instance is None:
        from mcp.server.fastmcp import FastMCP

        _mcp_instance = FastMCP("jenkins-build")
    return _mcp_instance


def __getattr__(name: str) -> Any:
    """模块级惰性属性访问（PEP 562）

    保持 ``from jenkins_config.mcp.server import mcp`` 的用法不变，
    同时把 mcp 依赖的导入推迟到首次访问 mcp 实例时。

    Args:
        name: 属性名

    Returns:
        name 为 "mcp" 时返回 FastMCP 实例

    Raises:
        AttributeError: 其他不存在的属性
    """
    if name == "mcp":
        return get_mcp()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _register_tools() -> None:
    """延迟导入并注册所有 MCP Tools 模块

    使用延迟导入模式避免启动时加载所有模块，
    各 tools 子模块在 import 时会自动通过 @mcp.tool() 注册到 mcp 实例。
    """
    from jenkins_config.mcp.tools import config_tools   # noqa: F401
    from jenkins_config.mcp.tools import history_tools  # noqa: F401
    from jenkins_config.mcp.tools import diagnose_tools  # noqa: F401
    from jenkins_config.mcp.tools import build_tools    # noqa: F401
    from jenkins_config.mcp.tools import where_tools    # noqa: F401
    from jenkins_config.mcp.tools import doctor_tools   # noqa: F401
    from jenkins_config.mcp.tools import init_tools     # noqa: F401
    from jenkins_config.mcp import resources             # noqa: F401
    from jenkins_config.mcp import prompts               # noqa: F401


def main() -> None:
    """MCP Server 入口

    先检测 mcp 依赖是否安装，缺失时输出友好错误并退出；
    否则配置日志（stderr / 可选文件）、注册所有 tools，
    再以 stdio 传输模式启动 MCP Server。
    """
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("缺少 mcp 依赖，请执行: pip install jenkins-config[mcp]", file=sys.stderr)
        sys.exit(1)
    setup_logging()
    _register_tools()
    get_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
