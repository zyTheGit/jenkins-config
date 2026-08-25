"""
MCP Prompts - 提示词模板

提供预定义的交互流程模板，
帮助 MCP 客户端引导用户完成常见操作。
"""

from jenkins_config.mcp.server import mcp


@mcp.prompt()
def build_workflow() -> str:
    """引导用户选择环境/项目并触发构建的交互流程

    Returns:
        提示词文本，按"环境 → 项目 → 触发 → 查状态"的顺序指导客户端

    Example:
        >>> "list_environments" in build_workflow()
        True
    """

    return """请按以下步骤协助用户进行 Jenkins 构建：

1. 首先使用 list_environments 工具查看可用的构建环境
2. 根据用户选择的环境，使用 list_projects 工具列出该项目
3. 确认用户要构建的项目、分支和参数
4. 使用 trigger_build 工具触发构建
5. 使用 get_build_status 工具查询构建进度
6. 如果构建失败，使用 get_build_log 工具获取日志并分析原因
"""


@mcp.prompt()
def diagnose_failure(build_log: str) -> str:
    """分析构建失败日志并给出修复建议

    Args:
        build_log: 构建日志内容

    Returns:
        含日志正文的提示词文本，引导按错误类型/位置/修复方案/预防措施分析

    Example:
        >>> "missing script" in diagnose_failure("npm ERR! missing script: build")
        True
    """

    return f"""请分析以下 Jenkins 构建日志，找出失败原因并给出修复建议：

{build_log}

请从以下角度分析：
1. 错误类型（编译错误、依赖问题、配置错误、网络问题等）
2. 具体的错误信息和位置
3. 可能的修复方案
4. 预防措施
"""
