"""
MCP Tools - 配置查询与管理工具

提供环境列表、项目列表、配置摘要的查询功能，
以及配置文件的保存功能。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import (
    environments_payload,
    get_config,
    projects_payload,
    resolve_config_path,
    write_allowed,
    write_denied_message,
)


@mcp.tool()
def list_environments(config_path: str = "") -> list[dict[str, str]]:
    """列出所有配置的环境

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        环境信息列表，每项包含 name（环境名称）和 description（描述）

    Example:
        >>> list_environments()  # doctest: +SKIP
        [{'name': 'dev', 'description': '开发环境'}]
    """
    try:
        return environments_payload(get_config(config_path))
    except Exception as e:
        return [{"name": "error", "description": f"加载配置失败: {e}"}]


@mcp.tool()
def list_projects(env: str = "", config_path: str = "") -> list[dict[str, str]]:
    """列出配置中的项目

    Args:
        env: 按环境名称过滤，为空时列出所有环境的项目
        config_path: 配置文件路径，为空时自动检测

    Returns:
        项目列表，每项包含 environment（环境名）、name（项目名）和 path（Job 路径）

    Example:
        >>> list_projects("dev")  # doctest: +SKIP
        [{'environment': 'dev', 'name': 'project-a', 'path': 'project-a'}]
    """
    try:
        return projects_payload(get_config(config_path), env or None)
    except Exception as e:
        return [{"environment": "error", "name": "error", "path": f"加载配置失败: {e}"}]


@mcp.tool()
def show_config(config_path: str = "") -> dict[str, Any]:
    """显示配置摘要（token 已脱敏）

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        配置摘要字典，包含 server_url、username、environments 和 build_config

    Example:
        >>> show_config()["token"]  # doctest: +SKIP
        '*** (长度 34)'
    """
    try:
        config = get_config(config_path)

        # token 完全脱敏：不保留任何原始字符，仅标注长度
        raw_token = config.server.token
        masked_token = f"*** (长度 {len(raw_token)})" if raw_token else "***"

        environments = environments_payload(config)

        return {
            "server_url": config.server.url,

            "username": config.server.username,
            "token": masked_token,
            "environments": environments,
            "build_config": {
                "mode": config.build.mode,
                "poll_interval": config.build.poll_interval,
                "queue_timeout": config.build.queue_timeout,
                "build_timeout": config.build.build_timeout,
                "curl_timeout": config.build.curl_timeout,
                "max_parallel": config.build.max_parallel,
                "log_dir": config.build.log_dir,
                "log_retention_days": config.build.log_retention_days,
            },
        }
    except Exception as e:
        return {"error": f"加载配置失败: {e}"}


@mcp.tool()
def save_config(config_path: str = "") -> dict[str, str]:
    """将当前配置规范化写回配置文件（危险操作：会覆写配置文件）

    读取当前配置并将其序列化写回 YAML 文件，可用于把旧格式（如顶层 branch、
    git_param）规范化为当前 params 结构。注意：注释与原有排版不会保留。
    仅支持保存到 .yaml/.yml 路径，避免覆写 JSON 等其他格式导致文件损坏。
    写操作，需先设置环境变量 JENKINS_MCP_ALLOW_WRITE=1 才会执行；
    覆写前会生成同名 .bak 备份，写入本身为原子替换。

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        包含 message（成功消息）、path（文件路径）和 backup（备份路径）的字典，
        或包含 error（错误信息）的字典

    Example:
        >>> save_config()  # doctest: +SKIP
        {'message': '配置已保存', 'path': '...', 'backup': '...bak'}
    """
    try:
        if not write_allowed():
            return {"error": write_denied_message("覆写配置文件")}

        resolved = resolve_config_path(config_path)
        if not resolved.lower().endswith((".yaml", ".yml")):
            return {"error": f"仅支持保存为 YAML 格式，当前路径: {resolved}"}

        target = Path(resolved)
        config = get_config(config_path)

        backup = ""
        if target.exists():
            backup_path = target.parent / f"{target.name}.bak"
            backup_path.write_bytes(target.read_bytes())
            backup = str(backup_path)

        config.save(resolved)
        return {"message": "配置已保存", "path": resolved, "backup": backup}
    except Exception as e:
        return {"error": f"保存配置失败: {e}"}
