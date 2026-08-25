"""
MCP Tools - 构建历史查询工具

提供构建历史记录和统计数据的查询功能。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import history_manager


def _get_history_manager(config_path: str = "") -> Any:
    """创建只读的 HistoryManager

    实现委托给 utils.history_manager，保证 create=False 这一
    "只读查询无副作用"的约定只有一处定义。

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        HistoryManager 实例

    Example:
        >>> _get_history_manager("/tmp/jenkins-config.yaml")  # doctest: +SKIP
        <jenkins_config.history.HistoryManager object at ...>
    """
    return history_manager(config_path)


@mcp.tool()
def show_history(env: str = "", limit: int = 20, config_path: str = "") -> list[dict[str, Any]]:
    """查询构建历史记录

    Args:
        env: 按环境名称过滤，为空时返回所有环境的记录
        limit: 返回的最大记录数量，默认 20
        config_path: 配置文件路径，为空时自动检测

    Returns:
        BuildRecord 字典列表，按时间倒序排列

    Example:
        >>> show_history(env="dev", limit=5)  # doctest: +SKIP
        [{'timestamp': '2026-03-20T10:00:00', 'env': 'dev', ...}]
    """
    try:
        manager = _get_history_manager(config_path)
        records = manager.list(env=env or None, limit=limit)
        return [asdict(r) for r in records]
    except Exception as e:
        return [{"error": f"查询历史记录失败: {e}"}]


@mcp.tool()
def show_history_stats(config_path: str = "") -> dict[str, Any]:
    """查询构建历史统计

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        统计字典，包含 total（总数）、success（成功数）、failure（失败数）、
        building（未落终态的占位记录数）和 success_rate（成功率百分比，
        分母已剔除 building）

    Example:
        >>> show_history_stats()  # doctest: +SKIP
        {'total': 10, 'success': 8, 'failure': 2, 'building': 0, 'success_rate': '80.0%'}
    """
    try:
        manager = _get_history_manager(config_path)
        stats = manager.stats()
        # 将 success_rate 格式化为百分比字符串
        stats["success_rate"] = f"{stats['success_rate']}%"
        return stats
    except Exception as e:
        return {"error": f"查询历史统计失败: {e}"}
