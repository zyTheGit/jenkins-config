"""
MCP 统一失败载荷 - 错误码、可执行的下一步建议、异常分类

所有 tool 的失败返回都从这里取字段，目的只有一个：让调用方（AI 客户端）
不必读中文错误文案就能决定下一步做什么。因此 error_code 是**闭集**，
next_steps 每条都必须是可执行动作（调某个 tool / 设某个环境变量 / 改某个文件），
而不是"请检查配置"这类无从下手的描述。

本模块刻意不导入 jenkins_config.mcp.utils：utils 需要在 inspect_config 里
引用这里的错误码，两边互相导入会成环。依赖方向固定为 utils → errors。
"""

from __future__ import annotations

from typing import Any

from jenkins_config.config_io import VALIDATION_ERROR_PREFIX

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
        # 项目缺 name 键（config_io._build_project）
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
