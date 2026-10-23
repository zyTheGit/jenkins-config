"""
MCP 统一失败载荷 - 错误码、可执行的下一步建议、端到端的载荷构造

所有 tool / resource 的失败返回都从本模块取字段，目的只有一个：让调用方（AI 客户端）
不必读中文错误文案就能决定下一步做什么。因此 error_code 是**闭集**，
next_steps 每条都必须是可执行动作（调某个 tool / 设某个环境变量 / 改某个文件），
而不是"请检查配置"这类无从下手的描述。

分类与构造也全部收在这里，调用方只交回"手头的东西"，不再自行拼载荷：
- 已有错误码 → failure_payload()
- 只有异常（tool 的 except 分支）→ config_failure_payload()
- 已有分层探测结论 → unusable_config_payload()
判定规则（哪些码算"配置本身不可用"、异常怎么折算成码）与载荷构造放在同一处，
才不会出现"探测说 A、载荷写 B"或某处漏拦一个码这类分叉。

模块级依赖方向固定为 utils → errors（utils 的 inspect_config 要引用这里的错误码）。
errors 里唯一需要反向调用的是路径解析，因此 config_failure_payload 在**函数内**惰性
导入 resolve_config_path：加载期仍然不成环，运行时两个模块都已就位。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jenkins_config.config_load import VALIDATION_ERROR_PREFIX

if TYPE_CHECKING:
    from jenkins_config.mcp.utils import ConfigInspection

# 排障文档位置（随失败载荷回传，便于客户端把用户引到完整说明）
DOCS_REF = "docs/mcp/README.md"


class ErrorCode:
    """失败载荷的错误码闭集

    分类维度是"用户下一步该做什么"，而不是"底层抛了什么异常"：
    config_path_denied 与 config_permission_denied 的底层异常同为
    PermissionError，但前者要改 JENKINS_MCP_CONFIG_ROOTS、后者要改文件权限。
    """

    CONFIG_NOT_FOUND = "config_not_found"
    CONFIG_PARSE_ERROR = "config_parse_error"
    CONFIG_PATH_DENIED = "config_path_denied"
    CONFIG_PERMISSION_DENIED = "config_permission_denied"
    CONFIG_INCOMPLETE = "config_incomplete"
    HOME_UNAVAILABLE = "home_unavailable"
    CONFIG_EXISTS = "config_exists"
    WRITE_NOT_ALLOWED = "write_not_allowed"
    INVALID_TARGET = "invalid_target"
    # 架构定义之外的兜底码：历史文件损坏、Jenkins 网络异常这类失败不属于
    # 上面任何一类，若强行归类会给出误导性的 next_steps。
    UNKNOWN = "unknown_error"


# "配置本身不可用"的错误码子集
#
# 命中这些码时，任何依赖配置的读操作都无从进行，调用方必须先把配置修好；
# 因此 tool 在真正取数据之前就该短路返回失败载荷，而不是回一个"看起来正常的
# 空结果"——空结果会让模型把"没配置"读成"确实没有数据"，顺着错误前提继续推理。
#
# config_incomplete 刻意不在其中：凭据没填只影响访问 Jenkins，
# 读本地历史文件这类操作照样成立。
#
# 这份子集只由 is_config_unusable() 与 unusable_config_payload() 消费：
# 判定不散落到各 tool，才能一处改动、处处生效。
CONFIG_UNUSABLE_CODES = frozenset({
    ErrorCode.CONFIG_NOT_FOUND,
    ErrorCode.CONFIG_PARSE_ERROR,
    ErrorCode.CONFIG_PATH_DENIED,
    ErrorCode.CONFIG_PERMISSION_DENIED,
    ErrorCode.HOME_UNAVAILABLE,
})


# 各错误码的默认下一步动作（每条都必须是能直接执行的动作）
NEXT_STEPS: dict[str, list[str]] = {
    ErrorCode.CONFIG_NOT_FOUND: [
        "调用 where_config 查看候选目录顺序与实际探测到的路径",
        "调用 init_config 在用户级目录 ~/.jenkins-config 生成配置模板",
        "或在客户端 mcp.json 的 env 中设置 JENKINS_MCP_CONFIG 为配置文件绝对路径",
    ],
    ErrorCode.CONFIG_PARSE_ERROR: [
        "调用 where_config 确认正在读取的配置文件路径",
        "用 YAML/JSON 校验器逐行查看该文件语法（缩进、引号、冒号后空格）",
        "调用 doctor 复查修复结果",
    ],
    ErrorCode.CONFIG_PATH_DENIED: [
        "改用 allowed_config_bases 之内的路径（调用 where_config 查看该列表）",
        "或在客户端 mcp.json 的 env 中把该目录追加到 JENKINS_MCP_CONFIG_ROOTS",
    ],
    ErrorCode.CONFIG_PERMISSION_DENIED: [
        "确认该路径是文件而非目录（调用 where_config 查看实际路径）",
        "给当前用户补齐该文件的读权限后调用 doctor 复查",
    ],
    ErrorCode.CONFIG_INCOMPLETE: [
        "编辑配置文件，把 server.url / server.token 从模板占位符改为真实取值",
        "调用 doctor 确认 config_complete 变为 ok",
    ],
    ErrorCode.HOME_UNAVAILABLE: [
        "为进程设置 HOME（Windows 为 USERPROFILE）环境变量",
        "或在客户端 mcp.json 的 env 中设置 JENKINS_MCP_CONFIG 为配置文件绝对路径，绕开用户级目录",
    ],
    ErrorCode.CONFIG_EXISTS: [
        "调用 where_config 查看现有配置的路径与来源",
        "确需覆盖时调用 init_config 并传 overwrite=true（需先设置 JENKINS_MCP_ALLOW_WRITE=1）",
    ],
    ErrorCode.WRITE_NOT_ALLOWED: [
        "在客户端 mcp.json 的 env 中设置 JENKINS_MCP_ALLOW_WRITE=1",
        "重启 MCP Server 使环境变量生效后重试",
    ],
    ErrorCode.INVALID_TARGET: [
        "调用 list_environments / list_projects 确认可用的环境名与项目名",
        "用确认后的取值重新调用本 tool",
    ],
    ErrorCode.UNKNOWN: [
        "调用 doctor 获取完整本地体检报告",
        "查看 MCP Server 的 stderr 日志（可设 JENKINS_MCP_LOG_LEVEL=DEBUG 提高详细度）",
    ],
}


def failure_payload(
    code: str,
    error: str,
    config_path: str = "",
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    """构造统一失败载荷

    next_steps 留空时按 code 取默认动作，而不是留空数组：调用方拿到的每个失败
    都必须自带出路，否则 AI 客户端只能把中文文案原样转述给用户。

    Args:
        code: ErrorCode 中的错误码；未登记的取值按 UNKNOWN 取默认动作
        error: 人类可读的错误描述
        config_path: 相关配置文件的绝对路径，未知时留空
        next_steps: 显式指定的下一步动作列表，为 None 时按 code 生成

    Returns:
        含 error_code、error、config_path、next_steps、docs 五个键的字典

    Example:
        >>> payload = failure_payload(ErrorCode.WRITE_NOT_ALLOWED, "已禁止写操作")
        >>> payload["error_code"], len(payload["next_steps"]) > 0
        ('write_not_allowed', True)
    """
    steps = next_steps if next_steps else NEXT_STEPS.get(code, NEXT_STEPS[ErrorCode.UNKNOWN])
    return {
        "error_code": code,
        "error": error,
        "config_path": config_path,
        "next_steps": list(steps),
        "docs": DOCS_REF,
    }


def failure_text(action: str, reason: BaseException | str) -> str:
    """拼接"动作: 原因"形式的失败文案

    action 为空时直接回原因，而不是留下 ": 原因" 这种开头带悬空冒号的文案。
    收在这里而不是各 tool 自行 f-string：失败文案的形状只有一种。

    Args:
        action: 人类可读的动作前缀，如 "加载配置失败"
        reason: 异常对象或已成型的原因文本

    Returns:
        "动作: 原因"；action 为空时为原因本身

    Example:
        >>> failure_text("加载配置失败", "语法错误")
        '加载配置失败: 语法错误'
        >>> failure_text("", "语法错误")
        '语法错误'
    """
    text = str(reason)
    return f"{action}: {text}" if action else text


def is_config_unusable(payload: dict[str, Any]) -> bool:
    """判断失败载荷是否属于"配置本身不可用"

    Args:
        payload: failure_payload() 的返回体，允许缺键

    Returns:
        error_code 命中 CONFIG_UNUSABLE_CODES 时 True

    Example:
        >>> is_config_unusable(failure_payload(ErrorCode.CONFIG_NOT_FOUND, "x"))
        True
        >>> is_config_unusable(failure_payload(ErrorCode.CONFIG_INCOMPLETE, "x"))
        False
    """
    return payload.get("error_code") in CONFIG_UNUSABLE_CODES


def classify(exc: BaseException, phase: str) -> str:
    """把异常按发生阶段折算为错误码

    必须传 phase：白名单越界与文件系统权限失败都抛 PermissionError，
    靠异常类型无法区分，但两者的修复动作完全不同。越界发生在任何文件读取
    之前（resolve_config_path 内），因此"阶段"是可靠的判别依据。

    Windows 上 `Path.read_text()` 对目录抛 PermissionError、Linux 抛
    IsADirectoryError，两者都是 OSError 子类，统一按 phase='read' 归入
    config_permission_denied，避免把平台差异漏进错误码。

    Args:
        exc: 捕获到的异常
        phase: 发生阶段，取 'resolve' / 'read' / 'parse' / 'validate'

    Returns:
        ErrorCode 中的错误码；无法归类时返回 ErrorCode.UNKNOWN

    Example:
        >>> classify(PermissionError("越界"), "resolve")
        'config_path_denied'
        >>> classify(PermissionError("无读权限"), "read")
        'config_permission_denied'
    """
    import yaml

    if phase == "resolve" and isinstance(exc, PermissionError):
        return ErrorCode.CONFIG_PATH_DENIED
    if phase == "resolve" and isinstance(exc, RuntimeError):
        # Path.home() / expanduser 在 HOME、USERPROFILE 均缺失时抛 RuntimeError。
        # 只在寻址阶段这么认：读取/解析阶段的 RuntimeError 与 HOME 无关，
        # 把它也报成 home_unavailable 会把调用方引向一条根本无效的修复动作
        return ErrorCode.HOME_UNAVAILABLE
    if isinstance(exc, FileNotFoundError):
        return ErrorCode.CONFIG_NOT_FOUND
    if isinstance(exc, KeyError):
        # 项目缺 name 键（config_load._build_project）
        return ErrorCode.CONFIG_INCOMPLETE
    if isinstance(exc, ValueError):
        if str(exc).startswith(VALIDATION_ERROR_PREFIX):
            return ErrorCode.CONFIG_INCOMPLETE
        return ErrorCode.CONFIG_PARSE_ERROR
    if isinstance(exc, yaml.YAMLError):
        return ErrorCode.CONFIG_PARSE_ERROR
    if isinstance(exc, OSError):
        return ErrorCode.CONFIG_PERMISSION_DENIED
    return ErrorCode.UNKNOWN


def config_failure_payload(
    config_path: str = "", exc: BaseException | None = None, action: str = ""
) -> dict[str, Any]:
    """把配置相关的异常折算为统一失败载荷

    tool 的 except 分支通常只拿得到一个异常对象，因此这里负责把"异常 + 手头
    的 config_path"补全成完整载荷：先重跑一次路径解析来定阶段——白名单越界与
    文件权限失败都抛 PermissionError，靠异常类型分不出来。重跑只做寻址、不读
    文件内容，代价可忽略，换来错误码的确定性。

    Args:
        config_path: 调用方传入的配置文件路径，为空时按环境变量 / 自动探测
        exc: 捕获到的异常，为 None 时按 UNKNOWN 处理
        action: 人类可读的动作前缀，如 "加载配置失败"

    Returns:
        failure_payload() 的五字段字典

    Example:
        >>> config_failure_payload(exc=FileNotFoundError("x"))["error_code"]
        'config_not_found'
    """
    from jenkins_config.mcp.utils import resolve_config_path

    try:
        resolved = resolve_config_path(config_path)
    except (PermissionError, RuntimeError) as denied:
        # 寻址阶段就失败：路径本身是问题所在，config_path 留空而不是回一个
        # 没通过校验的越界路径
        return failure_payload(classify(denied, "resolve"), failure_text(action, denied))

    if exc is None:
        return failure_payload(ErrorCode.UNKNOWN, action or "未知错误", resolved)
    return failure_payload(classify(exc, "read"), failure_text(action, exc), resolved)


def unusable_config_payload(
    inspection: ConfigInspection, action: str
) -> dict[str, Any] | None:
    """按分层探测结论决定是否短路为失败载荷

    短路规则归本模块而不是归调用方：CONFIG_UNUSABLE_CODES 的含义就是"读数据之前
    必须先修配置"，把子集和判定放在一起，才不会出现在某处忘拦 config_parse_error、
    另一处又误拦 config_incomplete 的分叉。凭据没填（config_incomplete）不拦，
    读本地历史的 tool 照样返回空结果。

    错误码与路径直接取 inspection 的分层结论，不再重跑一次分类，避免同一份配置
    被探测两次、两次的说法还不一样。

    Args:
        inspection: utils.inspect_config() 的返回体
        action: 人类可读的动作前缀，如 "查询历史记录失败"

    Returns:
        配置不可用时返回统一失败载荷；可用时返回 None

    Example:
        >>> from jenkins_config.mcp.utils import inspect_config  # doctest: +SKIP
        >>> unusable_config_payload(inspect_config("/nope/jenkins-config.yaml"), "查询失败")  # doctest: +SKIP
        {'error_code': 'config_not_found', ...}
    """
    if inspection.error_code not in CONFIG_UNUSABLE_CODES:
        return None
    return failure_payload(
        inspection.error_code,
        failure_text(action, inspection.error),
        inspection.config_path,
    )
