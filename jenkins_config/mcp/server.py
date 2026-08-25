"""
Jenkins MCP Server - 将 Jenkins 自动构建工具暴露为 MCP Tools

使用 FastMCP API 提供标准的 Model Context Protocol 接口，
包括 Tools（构建操作）、Resources（配置数据）和 Prompts（交互模板）。

mcp 依赖采用延迟导入：未安装 mcp extra 时本模块仍可被导入，
仅在 main() 入口处检测依赖并给出友好提示。
"""

import sys
from typing import Any

# 延迟初始化的 FastMCP 实例（首次访问时创建）
_mcp_instance: Any = None


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
    from jenkins_config.mcp import resources             # noqa: F401
    from jenkins_config.mcp import prompts               # noqa: F401


def main() -> None:
    """MCP Server 入口

    先检测 mcp 依赖是否安装，缺失时输出友好错误并退出；
    否则注册所有 tools 后以 stdio 传输模式启动 MCP Server。
    """
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("缺少 mcp 依赖，请执行: pip install jenkins-config[mcp]", file=sys.stderr)
        sys.exit(1)
    _register_tools()
    get_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
