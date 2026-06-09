# jenkins_config/config_io.py
"""
配置 I/O 模块 - 负责配置文件的加载、保存和模板生成

本模块包含 Config 类的 I/O 相关方法和向后兼容处理。
"""

from __future__ import annotations
import json
import logging
from pathlib import Path

from .config_types import (
    Config,
    ServerConfig,
    BuildConfig,
    Environment,
    Project,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 加载
# ============================================================================


def load_config(config_path: str) -> Config:
    """
    从文件加载配置（自动检测 YAML/JSON）

    Args:
        config_path: 配置文件路径

    Returns:
        Config 对象

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

    return _from_dict(data)


def _from_dict(data: dict) -> Config:
    """从字典构建 Config 对象"""
    # ServerConfig
    server_data = data.get("server", {})
    server = ServerConfig(
        url=server_data.get("url", ""),
        username=server_data.get("username", "admin"),
        token=server_data.get("token", ""),
    )

    # BuildConfig
    build_data = data.get("build", {})
    build = BuildConfig(
        mode=build_data.get("mode", "parallel"),
        poll_interval=build_data.get("poll_interval", 10),
        build_timeout=build_data.get("build_timeout", 3600),
        curl_timeout=build_data.get("curl_timeout", 30),
        log_dir=build_data.get("log_dir", "./jenkins_logs"),
        log_retention_days=build_data.get("log_retention_days", 3),
    )

    # branch_field
    branch_field = data.get("branch_field", "branch")

    # Environments
    environments = {}
    for env_name, env_data in data.get("environments", {}).items():
        env = _build_environment(env_name, env_data, branch_field)
        environments[env_name] = env

    return Config(
        server=server,
        build=build,
        branch_field=branch_field,
        environments=environments,
    )


def _build_environment(
    env_name: str, env_data: dict, global_branch_field: str
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
    proj_data: dict,
    env_params: dict,
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


def _parse_params_field(params_value) -> dict:
    """
    解析参数字段，支持新旧两种格式

    新格式: {"BRANCH": "develop", "skip_tests": "false"}
    旧格式: "BRANCH=develop&skip_tests=false"
    """
    if isinstance(params_value, dict):
        return params_value
    if isinstance(params_value, str):
        if not params_value.strip():
            return {}
        result = {}
        for pair in params_value.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip()] = v.strip()
        return result
    return {}


# ============================================================================
# 保存和模板
# ============================================================================


def save_config(config: Config, path: str):
    """将配置写入 YAML 文件"""
    import yaml

    data = config_to_dict(config)
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "# Jenkins 构建工具配置文件\n"
            "# 推荐使用 YAML 格式（支持注释）\n"
            "# 所有 Jenkins 构建参数都放在 params 字典中\n"
            "# 新增插件参数只需在 params 中添加，无需修改代码\n"
            "#\n"
            "# 参数合并优先级: 项目 params > 环境 params\n"
            "# 分支覆写: CLI -b 参数会覆盖 params 中 branch_field 指定的值\n\n"
        )
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            indent=2,
            width=80,
        )


def config_to_dict(config: Config) -> dict:
    """将 Config 对象序列化为字典"""
    environments = {}
    for env_name, env in config.environments.items():
        env_dict = {}
        if env.description:
            env_dict["description"] = env.description
        if env.branch_field:
            env_dict["branch_field"] = env.branch_field
        if env.params:
            env_dict["params"] = dict(env.params)
        if env.projects:
            env_dict["projects"] = [
                _project_to_dict(p) for p in env.projects
            ]
        environments[env_name] = env_dict

    result = {
        "server": {
            "url": config.server.url,
            "username": config.server.username,
            "token": config.server.token,
        },
        "build": {
            "mode": config.build.mode,
            "poll_interval": config.build.poll_interval,
            "build_timeout": config.build.build_timeout,
            "curl_timeout": config.build.curl_timeout,
            "log_dir": config.build.log_dir,
            "log_retention_days": config.build.log_retention_days,
        },
    }

    if config.branch_field != "branch":
        result["branch_field"] = config.branch_field

    if environments:
        result["environments"] = environments

    return result


def _project_to_dict(p: Project) -> dict:
    """将 Project 转为字典（移除 None 值）"""
    d = {"name": p.name}
    if p.path and p.path != p.name:
        d["path"] = p.path
    if p.params:
        d["params"] = dict(p.params)
    return d


# ============================================================================
# 模板
# ============================================================================


def generate_template() -> dict:
    """生成最小配置文件模板字典"""
    return {
        "server": {
            "url": "http://your-jenkins-server:8080",
            "username": "admin",
            "token": "your-api-token",
        },
        "build": {
            "mode": "parallel",
            "poll_interval": 10,
            "build_timeout": 3600,
            "curl_timeout": 30,
            "log_dir": "./jenkins_logs",
            "log_retention_days": 3,
        },
        "branch_field": "branch",
        "environments": {
            "dev": {
                "description": "开发环境",
                "params": {"branch": "develop"},
                "projects": [{"name": "project-a"}],
            },
            "prod": {
                "description": "生产环境",
                "params": {"branch": "main"},
                "projects": [{"name": "project-a-prod"}],
            },
        },
    }


def show_template():
    """打印配置文件模板说明"""
    lines = [
        "=" * 64,
        "  Jenkins 配置文件模板 (jenkins-config.yaml)",
        "=" * 64,
        "",
        "  server:        （必填）Jenkins 服务器配置",
        "    url:         必填  Jenkins 地址",
        "    token:       必填  API Token",
        "    username     可选  默认: admin",
        "",
        "  build:         （可选）构建行为配置",
        "    mode               可选  parallel(默认) / sequential",
        "    poll_interval      可选  轮询间隔秒数 (默认: 10)",
        "    build_timeout      可选  构建超时秒数 (默认: 3600)",
        "    curl_timeout       可选  HTTP超时秒数 (默认: 30)",
        "    log_dir            可选  日志目录 (默认: ./jenkins_logs)",
        "    log_retention_days 可选  日志保留天数 (默认: 3)",
        "",
        "  branch_field:  （可选）CLI -b 使用的参数名 (默认: branch)",
        "                  如 BRANCH、GIT_BRANCH 等",
        "",
        "  environments:  （必填）环境配置字典",
        "    <env_name>:",
        "      description   可选  环境描述",
        "      branch_field  可选  覆盖全局 branch_field",
        "      params:       可选  环境参数（字典，新增插件只需加键值对）",
        "        <key>: <value>",
        "      projects:     （必填）项目列表",
        "        - name:     必填  项目名称",
        "          path:     可选  Job 路径（默认同 name）",
        "          params:   可选  项目参数（覆盖环境同名参数）",
        "            <key>: <value>",
        "",
        "-" * 64,
        "  参数合并: 项目 params > 环境 params",
        "  分支覆写: CLI -b 会覆盖 params 中 branch_field 指定的键",
        "  新增插件: 直接在 params 中添加键值对，无需修改代码",
        "-" * 64,
    ]
    print("\n".join(lines))
