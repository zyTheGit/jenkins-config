"""
MCP Tools - Jenkins 诊断查询工具

提供 Jenkins 连接检测、构建状态查询和构建日志获取功能。
"""

from __future__ import annotations

from typing import Any

from jenkins_config.mcp.errors import CONFIG_UNUSABLE_CODES, ErrorCode, failure_payload
from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import config_failure_payload, jenkins_client
from jenkins_config.utils import format_duration_short

# 构建日志默认返回的尾部大小（KB），避免超大日志占满内存与模型上下文
DEFAULT_LOG_TAIL_KB = 50

# Jenkins 不可达时的下一步动作
#
# 不套用 classify()：requests 的连接异常都是 OSError 子类，会被归成
# config_permission_denied，给出"补齐文件读权限"这种完全跑偏的建议。
# 网络失败与配置失败必须分开处理。
UNREACHABLE_STEPS = [
    "调用 doctor 确认配置与凭据是否完整（默认不发网络请求）",
    "确认 server.url 可从本机访问（代理 / VPN / 防火墙）",
    "确认 server.token 未过期",
]

# get_build_log 失败文案统一附带的可执行动作
#
# 该 tool 的返回类型是 str（对外接口，本轮不改），装不下结构化的 next_steps，
# 所以把动作直接写进文案：调用方至少能照着做，而不是只拿到一句"失败了"。
LOG_FAILURE_HINT = "下一步：调用 doctor 查看本地环境诊断，或调用 where_config 确认配置来源。"


def _log_failure_text(reason: str, payload: dict[str, Any] | None = None) -> str:
    """把 get_build_log 的失败原因渲染为带可执行动作的文本

    配置类失败额外写出 error_code 与 config_path：文本返回值没有结构化字段，
    把这两项显式印在文案里，模型才能判出"是配置问题"并复用既有的排障路径。

    Args:
        reason: 人类可读的失败原因
        payload: config_failure_payload() 的返回体，为 None 时只附通用动作

    Returns:
        末尾带可执行动作的失败文案

    Example:
        >>> "doctor" in _log_failure_text("获取构建日志失败: 超时")
        True
    """
    lines = [reason]
    if payload is not None and payload["error_code"] in CONFIG_UNUSABLE_CODES:
        lines.append(
            f"error_code={payload['error_code']}，config_path={payload['config_path']}"
        )
    lines.append(LOG_FAILURE_HINT)
    return "\n".join(lines)


@mcp.tool()
def health_check(config_path: str = "") -> dict[str, Any]:
    """检查 Jenkins 服务器是否可达

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        检测结果字典，包含 reachable（是否可达）和 url（服务器地址）；
        失败时额外合并统一失败载荷（error_code / error / config_path /
        next_steps / docs），reachable 与 url 两个既有键保持不变

    Example:
        >>> health_check()  # doctest: +SKIP
        {'reachable': True, 'url': 'http://jenkins.example.com'}
    """
    try:
        with jenkins_client(config_path) as client:
            url = getattr(client, "base_url", "")
            try:
                return {"reachable": client.health_check(), "url": url}
            except ConnectionError as e:
                result: dict[str, Any] = {"reachable": False, "url": url}
                result.update(failure_payload(
                    ErrorCode.UNKNOWN, f"连接失败: {e}", next_steps=UNREACHABLE_STEPS
                ))
                return result
    except Exception as e:
        result = {"reachable": False, "url": ""}
        result.update(config_failure_payload(config_path, e, "健康检查失败"))
        return result


@mcp.tool()
def get_build_status(job_path: str, build_num: int, config_path: str = "") -> dict[str, Any]:
    """查询 Jenkins 构建状态

    Args:
        job_path: Jenkins Job 路径，如 "my-project" 或 "folder/my-project"
        build_num: 构建编号
        config_path: 配置文件路径，为空时自动检测

    Returns:
        构建状态字典，包含 number（编号）、status（状态）、
        result（结果）和 duration（耗时描述）；失败时保留 number / status /
        result 三个既有键（形状不变），并在顶层合并统一失败载荷
        （error_code / error / config_path / next_steps / docs）

    Example:
        >>> get_build_status("my-project", 123)  # doctest: +SKIP
        {'number': 123, 'status': 'SUCCESS', 'result': 'SUCCESS', 'duration': '2m 5s'}
    """
    try:
        with jenkins_client(config_path) as client:
            info = client.get_build_status(job_path, build_num)

        return {
            "number": info.number,
            "status": info.status.value,
            "result": info.result,
            # 与 CLI 的中文时长格式互补，MCP 侧统一用英文缩写风格
            "duration": format_duration_short(info.duration),
        }
    except Exception as e:
        # 错误文案不再只塞在业务字段里：没有 error_code，调用方分不清
        # "查不到这次构建"和"配置压根加载不起来"，也拿不到下一步动作。
        result: dict[str, Any] = {
            "number": build_num,
            "status": "UNKNOWN",
            "result": None,
        }
        result.update(config_failure_payload(config_path, e, "查询构建状态失败"))
        return result


@mcp.tool()
def get_build_log(
    job_path: str,
    build_num: int,
    tail_kb: int = DEFAULT_LOG_TAIL_KB,
    config_path: str = "",
) -> str:
    """获取 Jenkins 构建日志（默认只返回尾部）

    Args:
        job_path: Jenkins Job 路径，如 "my-project" 或 "folder/my-project"
        build_num: 构建编号
        tail_kb: 返回的日志尾部大小（KB），默认 50；<=0 表示返回全量日志
            （构建日志常达数十 MB，全量返回会占满内存与模型上下文）
        config_path: 配置文件路径，为空时自动检测

    Returns:
        构建日志文本，被截断时开头带一行截断说明；失败时返回错误信息，
        文案末尾附可执行动作（doctor / where_config），配置类失败还会写出
        error_code 与 config_path

    Example:
        >>> get_build_log("my-project", 123, tail_kb=10)  # doctest: +SKIP
        '...（日志已截断...）\\nFinished: SUCCESS'
    """
    try:
        max_bytes = tail_kb * 1024 if tail_kb > 0 else None
        with jenkins_client(config_path) as client:
            log_text = client.get_build_log(job_path, build_num, max_bytes=max_bytes)
        if not log_text:
            return _log_failure_text(
                f"未获取到 Job '{job_path}' #{build_num} 的构建日志，"
                "请确认 Job 和构建编号是否正确。"
            )
        return log_text
    except Exception as e:
        payload = config_failure_payload(config_path, e, "获取构建日志失败")
        return _log_failure_text(payload["error"], payload)
