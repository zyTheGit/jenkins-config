"""
MCP Tools - 配置锚定诊断工具

回答"我这次到底读的是哪份配置、为什么是它"：暴露运行模式、候选目录顺序、
每个候选的命中情况、环境变量是否生效、以及配置白名单。

只做**路径层面**的探测，全程不加载配置文件内容，因此返回体里不可能出现
server.url / token 等字段——脱敏靠"不读"而不是靠"读完再抹掉"，
后者只要将来有人加一个字段就会漏。字段集合也刻意与 show_config 不重叠：
两个 tool 各答一个问题（配置内容 vs 配置来源），重叠会让客户端分不清该调哪个。

越界路径（不在 allowed_config_bases 之内）只回 path_allowed=false 与出路，
**不回 exists / history_path**：否则本 tool 会变成"任意路径存在性探针"，
绕过 resolve_config_path 已经建立的白名单。诊断能力损失有限——调用方真正
需要知道的是"这个路径不被允许、怎么放行"，而不是它在不在那儿；而同一进程内
留两套不一致的边界，会在传输形态从 stdio 变为远程时兑现为漏洞，
且那次改动不会有人回头审这个 tool。
"""

from __future__ import annotations

from typing import Any

from jenkins_config.mcp.errors import ErrorCode, failure_payload
from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import probe_report_for_mcp


@mcp.tool()
def where_config(config_path: str = "") -> dict[str, Any]:
    """诊断配置文件的锚定结果（只读路径信息，不读取配置内容）

    Args:
        config_path: 显式指定的配置文件路径，为空时按环境变量 / 自动探测

    Returns:
        含 config_path（绝对路径）、exists、path_allowed、source（explicit_arg /
        env_var / probed / fallback）、env_var（name / value / effective）、
        mode（source / frozen）、search_bases（候选目录明细）、
        candidate_file_names、history_path、allowed_config_bases 的字典；
        路径越界时省略 exists / history_path 并改带 error_code /
        next_steps 等失败载荷字段；探测本身失败时回 unknown_error 失败载荷

    Example:
        >>> where_config()["source"]  # doctest: +SKIP
        'probed'
    """
    try:
        report = probe_report_for_mcp(config_path)
        result = {
            "config_path": report["config_path"],
            "path_allowed": report["path_allowed"],
            "source": report["source"],
            "env_var": report["env_var"],
            "mode": report["mode"],
            "search_bases": report["bases"],
            "candidate_file_names": report["candidate_file_names"],
            "allowed_config_bases": report["allowed_config_bases"],
        }
        if not report["path_allowed"]:
            result.update(
                failure_payload(
                    ErrorCode.CONFIG_PATH_DENIED,
                    f"配置文件路径不在允许范围内: {report['config_path']}",
                    report["config_path"],
                )
            )
            return result

        result["exists"] = report["exists"]
        result["history_path"] = report["history_path"]
        return result
    except Exception as e:
        # 探测本身失败（例如 paths 层抛出未预期异常）也必须回五字段载荷：
        # 一个只带 error 的返回体让调用方无法机械判错，也拿不到下一步动作
        return failure_payload(
            ErrorCode.UNKNOWN,
            f"探测配置路径失败: {e}",
            config_path,
        )
