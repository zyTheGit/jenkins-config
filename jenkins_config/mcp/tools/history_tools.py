"""
MCP Tools - 构建历史查询工具

提供构建历史记录和统计数据的查询功能。

两个 tool 都在取 HistoryManager **之前**先做一次配置探测：历史文件路径由配置
锚定，而 resolve_history_path 对"配置根本不存在"这种情况照样能算出一个 fallback
路径，HistoryManager(create=False) 读不到文件时又不抛异常——两者叠加的结果是
"没配置"和"确实没构建过"都返回空列表 / 全 0 统计，调用方无法区分，模型会顺着
"没有历史"这个错误前提继续推理。因此配置类失败必须在取数据前短路成失败载荷。

反过来，配置正常而历史文件尚未生成是**合法状态**（还没触发过构建），
必须保持空列表 / 全 0 统计的既有行为，不能被上面的短路误伤。
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from jenkins_config.mcp.errors import CONFIG_UNUSABLE_CODES, failure_payload
from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import config_failure_payload, history_manager, inspect_config


def _config_gate(config_path: str, action: str) -> dict[str, Any] | None:
    """在读取历史数据前拦截"配置不可用"的情形

    只拦 CONFIG_UNUSABLE_CODES（文件不存在 / 越界 / 不可读 / 语法错误 /
    家目录不可用）。config_incomplete 不拦：凭据没填不影响读本地历史文件。

    错误码与路径直接取 inspect_config 的分层结论，不再重跑一次分类，
    避免"探测说 A、载荷写 B"的漂移。

    Args:
        config_path: 调用方传入的配置文件路径，为空时按环境变量 / 自动探测
        action: 人类可读的动作前缀，如 "查询历史记录失败"

    Returns:
        配置不可用时返回统一失败载荷；配置可用时返回 None

    Example:
        >>> _config_gate("", "查询历史记录失败")  # doctest: +SKIP
        {'error_code': 'config_not_found', ...}
    """
    inspection = inspect_config(config_path)
    if inspection.error_code in CONFIG_UNUSABLE_CODES:
        return failure_payload(
            inspection.error_code,
            f"{action}: {inspection.error}",
            inspection.config_path,
        )
    return None


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
        BuildRecord 字典列表，按时间倒序排列；失败时返回单元素列表，
        元素为统一失败载荷（只含 error_code / error / config_path / next_steps / docs）。
        配置正常但历史文件尚未生成时返回空列表（合法状态：还没触发过构建）

    Example:
        >>> show_history(env="dev", limit=5)  # doctest: +SKIP
        [{'timestamp': '2026-03-20T10:00:00', 'env': 'dev', ...}]
    """
    # 先探测配置：配置不可用时下面读到的"空历史"是假象，必须短路（见模块 docstring）
    gate = _config_gate(config_path, "查询历史记录失败")
    if gate is not None:
        return [gate]

    try:
        manager = _get_history_manager(config_path)
        records = manager.list(env=env or None, limit=limit)
        return [asdict(r) for r in records]
    except Exception as e:
        return [config_failure_payload(config_path, e, "查询历史记录失败")]


@mcp.tool()
def show_history_stats(config_path: str = "") -> dict[str, Any]:
    """查询构建历史统计

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        统计字典，包含 total（总数）、success（成功数）、failure（失败数）、
        building（未落终态的占位记录数）和 success_rate（成功率百分比，
        分母已剔除 building）；失败时顶层合并统一失败载荷。
        配置正常但历史文件尚未生成时返回全 0 统计（合法状态：还没触发过构建）

    Example:
        >>> show_history_stats()  # doctest: +SKIP
        {'total': 10, 'success': 8, 'failure': 2, 'building': 0, 'success_rate': '80.0%'}
    """
    # 与 show_history 同一理由：全 0 统计在配置缺失时是假象，不能当成"没构建过"
    gate = _config_gate(config_path, "查询历史统计失败")
    if gate is not None:
        return gate

    try:
        manager = _get_history_manager(config_path)
        stats = manager.stats()
        # 将 success_rate 格式化为百分比字符串
        stats["success_rate"] = f"{stats['success_rate']}%"
        return stats
    except Exception as e:
        return config_failure_payload(config_path, e, "查询历史统计失败")
