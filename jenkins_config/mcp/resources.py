"""
MCP Resources - 只读数据端点

提供配置和构建历史的只读数据资源，
MCP 客户端可通过 URI 模板访问这些数据。

与同名 Tool 共用 utils 中的数据组装函数；异常一律经 mcp.errors 折算成
统一失败载荷（error_code / error / config_path / next_steps / docs）再序列化，
客户端读 tool 还是读 resource 都只面对一种失败形状。
"""

from __future__ import annotations

from typing import Any, Callable

from jenkins_config.mcp.errors import config_failure_payload
from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import (
    dump_json,
    environments_payload,
    get_config,
    history_manager,
    history_path_for,
    projects_payload,
)


def _safe_dump(loader: Callable[[], Any], action: str) -> str:
    """执行数据加载并序列化，异常时回统一失败载荷的 JSON

    不再是 {"error": "..."} 两键形状：Resource URI 模板装不下 config_path，
    因此把 config_path 留空交给 errors 自行探测，但失败字段集合与 Tool 完全一致。

    Args:
        loader: 无参的数据加载函数
        action: 动作描述，用于拼接错误信息

    Returns:
        JSON 字符串，失败时为五字段失败载荷

    Example:
        >>> _safe_dump(lambda: {"ok": True}, "测试")
        '{\\n  "ok": true\\n}'
    """
    try:
        return dump_json(loader())
    except Exception as e:
        return dump_json(config_failure_payload("", e, action))


@mcp.resource("config://environments")
def get_environments_resource() -> str:
    """获取当前配置中的所有环境信息

    Returns:
        JSON 格式的环境列表，每项包含 name 和 description

    Example:
        >>> get_environments_resource()  # doctest: +SKIP
        '[\\n  {\\n    "name": "dev",\\n    "description": "开发环境"\\n  }\\n]'
    """
    return _safe_dump(lambda: environments_payload(get_config()), "读取环境列表失败")


@mcp.resource("config://projects/{env}")
def get_projects_resource(env: str) -> str:
    """获取指定环境的项目列表

    Args:
        env: 环境名称

    Returns:
        JSON 格式的项目列表，每项包含 environment、name 和 path

    Example:
        >>> get_projects_resource("dev")  # doctest: +SKIP
        '[\\n  {\\n    "environment": "dev", ...\\n]'
    """
    return _safe_dump(lambda: projects_payload(get_config(), env), "读取项目列表失败")


def _recent_builds_payload() -> Any:
    """组装最近构建记录数据

    Returns:
        最近 10 条构建记录列表；历史文件不存在时返回带 message 的字典

    Example:
        >>> _recent_builds_payload()  # doctest: +SKIP
        [{'timestamp': '2026-03-20T10:00:00', ...}]
    """
    manager, message = _history_or_message()
    if manager is None:
        return message

    records = manager.list(limit=10)
    return [
        {
            "timestamp": r.timestamp,
            "env": r.env,
            "job_key": r.job_key,
            "build_num": r.build_num,
            "status": r.status,
            "duration": r.duration,
            "project_name": r.project_name,
        }
        for r in records
    ]


def _history_or_message() -> tuple[Any, Any]:
    """获取只读 HistoryManager，文件缺失时给出提示载荷

    Returns:
        (HistoryManager, None)；历史文件不存在时返回 (None, 带 message 的字典)

    Example:
        >>> _history_or_message()  # doctest: +SKIP
        (<jenkins_config.history.HistoryManager object at ...>, None)
    """
    history_file = history_path_for()
    if not history_file.exists():
        return None, {"message": f"构建历史文件不存在: {history_file}"}
    return history_manager(history_file=str(history_file)), None


def _stats_payload() -> Any:
    """组装构建统计数据

    Returns:
        统计字典；历史文件不存在时返回带 message 的字典

    Example:
        >>> _stats_payload()  # doctest: +SKIP
        {'total': 10, 'success': 8, 'failure': 2, 'building': 0, 'success_rate': 80.0}
    """
    manager, message = _history_or_message()
    if manager is None:
        return message

    return manager.stats()


@mcp.resource("history://recent")
def get_recent_builds_resource() -> str:
    """获取最近的构建记录

    历史文件路径与 show_history 工具一致：锚定到配置文件所在目录的
    data/build_history.json。文件不存在时返回友好提示。

    Returns:
        JSON 格式的最近 10 条构建记录，或文件不存在时的提示信息

    Example:
        >>> get_recent_builds_resource()  # doctest: +SKIP
        '[\\n  {\\n    "timestamp": "2026-03-20T10:00:00", ...\\n]'
    """
    return _safe_dump(_recent_builds_payload, "读取构建历史失败")


@mcp.resource("history://stats")
def get_stats_resource() -> str:
    """获取构建统计摘要

    历史文件路径与 show_history_stats 工具一致：锚定到配置文件所在目录的
    data/build_history.json。文件不存在时返回友好提示。

    Returns:
        JSON 格式的统计信息（包含 total、success、failure、building、
        success_rate），或文件不存在时的提示信息

    Example:
        >>> get_stats_resource()  # doctest: +SKIP
        '{\\n  "total": 10, ...\\n}'
    """
    return _safe_dump(_stats_payload, "读取构建统计失败")
