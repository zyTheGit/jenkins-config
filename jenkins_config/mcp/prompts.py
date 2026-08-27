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


@mcp.prompt()
def setup_workflow() -> str:
    """引导用户从零完成本地配置直到 list_environments 成功的交互流程

    刻意把"编辑文件填真实取值"单列一步并停下来等用户：server.token 是凭据，
    不能由客户端猜或代填，模板生成后必须由人补上。

    Returns:
        提示词文本，按"doctor → where_config → init_config → 人工填字段 →
        list_environments 验证"的顺序指导客户端

    Example:
        >>> "init_config" in setup_workflow()
        True
    """

    return """请按以下五步协助用户完成 Jenkins MCP 的首次配置：

1. 调用 doctor 做一次本地体检，确认卡在哪一层（配置是否存在、是否填完、写开关状态）
2. 调用 where_config 查看候选目录顺序与当前会读到哪个路径
3. 若配置文件尚不存在，调用 init_config 生成模板（默认写入用户级目录
   ~/.jenkins-config；需要放在当前工作目录时传 target='cwd'）。
   若返回 config_exists，说明已有配置，不要覆盖，回到第 2 步确认路径
4. 请用户打开 init_config 返回的 path，把 server.url 与 server.token 从占位符改为
   真实取值（token 属于凭据，必须由用户本人填写，不要代填或猜测），
   并按 template_fields 中 required 的字段补齐 environments 下的环境与项目
5. 调用 list_environments 验证：能列出真实环境即配置生效；
   若仍失败，按返回体里的 error_code 与 next_steps 继续处理
"""
