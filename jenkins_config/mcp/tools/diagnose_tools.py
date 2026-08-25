"""
MCP Tools - Jenkins 诊断查询工具

提供 Jenkins 连接检测、构建状态查询和构建日志获取功能。
"""

from __future__ import annotations

from typing import Any

from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import jenkins_client
from jenkins_config.utils import format_duration_short

# 构建日志默认返回的尾部大小（KB），避免超大日志占满内存与模型上下文
DEFAULT_LOG_TAIL_KB = 50


@mcp.tool()
def health_check(config_path: str = "") -> dict[str, Any]:
    """检查 Jenkins 服务器是否可达

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        检测结果字典，包含 reachable（是否可达）和 url（服务器地址）

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
                return {"reachable": False, "url": url, "error": f"连接失败: {e}"}
    except Exception as e:
        return {"reachable": False, "url": "", "error": f"健康检查失败: {e}"}


@mcp.tool()
def get_build_status(job_path: str, build_num: int, config_path: str = "") -> dict[str, Any]:
    """查询 Jenkins 构建状态

    Args:
        job_path: Jenkins Job 路径，如 "my-project" 或 "folder/my-project"
        build_num: 构建编号
        config_path: 配置文件路径，为空时自动检测

    Returns:
        构建状态字典，包含 number（编号）、status（状态）、
        result（结果）和 duration（耗时描述）

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
        return {
            "number": build_num,
            "status": "UNKNOWN",
            "result": None,
            "error": f"查询构建状态失败: {e}",
        }


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
        构建日志文本，被截断时开头带一行截断说明；失败时返回错误信息

    Example:
        >>> get_build_log("my-project", 123, tail_kb=10)  # doctest: +SKIP
        '...（日志已截断...）\\nFinished: SUCCESS'
    """
    try:
        max_bytes = tail_kb * 1024 if tail_kb > 0 else None
        with jenkins_client(config_path) as client:
            log_text = client.get_build_log(job_path, build_num, max_bytes=max_bytes)
        if not log_text:
            return (
                f"未获取到 Job '{job_path}' #{build_num} 的构建日志，"
                "请确认 Job 和构建编号是否正确。"
            )
        return log_text
    except Exception as e:
        return f"获取构建日志失败: {e}"
