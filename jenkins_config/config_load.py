# jenkins_config/config_load.py
"""
配置加载模块 - 负责文件解析、必填校验与旧格式兼容

解析结果以 Config 的构造参数返回（``Config.load`` 直接展开），本模块不依赖
Config 类本身，避免与 config.py 形成导入环。
"""

from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path
from typing import Any

from .config_types import BuildConfig, Environment, Project, ServerConfig


logger = logging.getLogger(__name__)


# 必填字段校验失败的错误前缀
#
# mcp/errors.classify 靠它把"字段没填"从"文件格式不合法"里分出来：两者都是
# ValueError，异常类型无法区分，而给用户的下一步动作完全不同（填字段 vs 修语法）。
# 备选是新增一个专用异常类型，但那会改变 CLI 侧既有的 ValueError 捕获语义，
# 代价大于收益，因此选择把字面量收敛为常量、由两侧共用。
VALIDATION_ERROR_PREFIX = "配置错误: "


def parse_config_file(config_path: str) -> dict[str, Any]:
    """
    解析配置文件，返回 Config 的构造参数（自动检测 YAML/JSON）

    Args:
        config_path: 配置文件路径

    Returns:
        可直接展开为 ``Config(**parts)`` 的字典，包含 server / build /
        branch_field / environments

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 文件格式不支持或不合法
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    raw = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError(f"YAML 配置文件格式错误: {config_path}")
    elif path.suffix == ".json":
        data = json.loads(raw)
    else:
        data = json.loads(raw)

    return _config_parts(data)


def _dataclass_from_mapping(cls: type, data: dict[str, Any]) -> Any:
    """按 dataclass 字段名取值构造实例，未知键忽略"""
    names = {f.name for f in fields(cls)}
    return cls(**{key: value for key, value in data.items() if key in names})


def _config_parts(data: dict[str, Any]) -> dict[str, Any]:
    """从原始字典构建 Config 的构造参数"""
    # ServerConfig：url 缺失时留空串，交给 _validate_config 报同一句错误
    server_data = data.get("server", {})
    server = ServerConfig(
        url=server_data.get("url", ""),
        username=server_data.get("username", "admin"),
        token=server_data.get("token", ""),
    )

    # 验证必填字段
    _validate_config(server)

    # BuildConfig
    build = _dataclass_from_mapping(BuildConfig, data.get("build", {}))

    # branch_field
    branch_field = data.get("branch_field", "branch")

    # Environments
    environments = {}
    for env_name, env_data in data.get("environments", {}).items():
        env = _build_environment(env_name, env_data, branch_field)
        environments[env_name] = env

    return {
        "server": server,
        "build": build,
        "branch_field": branch_field,
        "environments": environments,
    }


def _validate_config(server: ServerConfig):
    """
    验证配置必填字段

    Args:
        server: 服务器配置对象

    Raises:
        ValueError: 必填字段为空时抛出
    """
    if not isinstance(server.url, str) or not server.url.strip():
        raise ValueError(f"{VALIDATION_ERROR_PREFIX}server.url 不能为空")
    if not isinstance(server.token, str) or not server.token.strip():
        raise ValueError(f"{VALIDATION_ERROR_PREFIX}server.token 不能为空")


def _build_environment(
    env_name: str, env_data: dict[str, Any], global_branch_field: str
) -> Environment:
    """
    构建 Environment 对象（含向后兼容）

    兼容旧格式字段: default_branch, git_param
    """
    old_default_branch = env_data.get("default_branch")
    old_git_param = env_data.get("git_param")
    env_params = _parse_params_field(env_data.get("params", ""))

    # 向后兼容：将旧字段合并到 params
    if old_default_branch or old_git_param:
        effective_field = env_data.get(
            "branch_field", old_git_param or global_branch_field
        )
        if old_default_branch and effective_field not in env_params:
            env_params = dict(env_params)
            env_params[effective_field] = old_default_branch
            logger.warning(
                "环境 '%s' 使用了弃用的 'default_branch' 字段，"
                "请迁移到 params.%s（参见 CHANGELOG.md）",
                env_name,
                effective_field,
            )

    env_branch_field = env_data.get("branch_field", "")

    # 向后兼容：迁移 git_param → branch_field
    if not env_branch_field and old_git_param:
        env_branch_field = old_git_param

    projects = []
    for proj_data in env_data.get("projects", []):
        project = _build_project(
            proj_data, env_params, env_branch_field or global_branch_field, env_name
        )
        projects.append(project)

    return Environment(
        name=env_name,
        description=env_data.get("description", ""),
        branch_field=env_branch_field,
        params=env_params,
        projects=projects,
    )


def _build_project(
    proj_data: dict[str, Any],
    env_params: dict[str, Any],
    effective_branch_field: str,
    env_name: str,
) -> Project:
    """
    构建 Project 对象（含向后兼容）

    兼容旧格式字段: branch, git_param
    """
    proj_params = _parse_params_field(proj_data.get("params", ""))

    old_branch = proj_data.get("branch")
    old_git_param = proj_data.get("git_param")

    if old_branch or old_git_param:
        param_key = old_git_param or effective_branch_field
        proj_params = dict(proj_params)
        if old_branch and param_key not in proj_params:
            proj_params[param_key] = old_branch
            # 如果项目使用自定义 git_param（不同于环境的 branch_field），
            # 同时将分支值设置到环境级别的 branch_field 中，
            # 确保 job.branch 派生正确
            if old_git_param and old_git_param != effective_branch_field:
                proj_params[effective_branch_field] = old_branch
            logger.warning(
                "项目 '%s'（环境 '%s'）使用了弃用的 'branch' 字段，"
                "请迁移到 params.%s（参见 CHANGELOG.md）",
                proj_data["name"],
                env_name,
                param_key,
            )

    return Project(
        name=proj_data["name"],
        path=proj_data.get("path", proj_data["name"]),
        params=proj_params,
    )


def _parse_params_field(params_value: object) -> dict[str, Any]:
    """
    解析参数字段，支持新旧两种格式

    新格式: {"BRANCH": "develop", "skip_tests": "false"}
    旧格式: "BRANCH=develop&skip_tests=false"
    """
    if isinstance(params_value, dict):
        return {str(k): v for k, v in params_value.items()}
    if isinstance(params_value, str):
        if not params_value.strip():
            return {}
        result: dict[str, Any] = {}
        for pair in params_value.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip()] = v.strip()
        return result
    return {}
