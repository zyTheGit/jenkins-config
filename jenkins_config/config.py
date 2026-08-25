# jenkins_config/config.py
"""
配置模块 - 配置类型、I/O 和业务逻辑的汇总入口

本模块重新导出所有配置类型，并提供 job 相关的业务方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .config_io import (
    config_to_dict,
    generate_template,
    load_config as _load_config,
    save_config as _save_config,
    show_template,
)

# 重新导出所有类型
from .config_types import (
    Config,
    Job,
    Project,
)

if TYPE_CHECKING:
    from .history import BuildRecord


# ============================================================================
# 将 I/O 方法附加到 Config 类（保持 Config.load() 调用方式不变）
# ============================================================================

Config.load = classmethod(lambda cls, path: _load_config(path))  # type: ignore[assignment]
Config.save = lambda self, path: _save_config(self, path)  # type: ignore[assignment]
Config.to_dict = lambda self: config_to_dict(self)  # type: ignore[assignment]
Config.generate_template = staticmethod(lambda: generate_template())  # type: ignore[assignment]
Config.show_template = staticmethod(lambda: show_template())  # type: ignore[assignment]


# ============================================================================
# Job 业务方法
# ============================================================================


def _branch_field_for(self: Config, env: Optional[str] = None) -> str:
    """
    获取指定环境生效的分支参数名

    优先级: 环境级 branch_field > 全局 branch_field。
    这是分支字段解析的唯一入口，CLI 与 MCP Server 都应通过它取值，
    避免在调用方各自重复实现优先级规则。

    Args:
        env: 环境名称，为空或环境不存在时退回全局配置

    Returns:
        分支参数名，如 "branch" 或 "BRANCH_NAME"

    Example:
        >>> config.branch_field_for("dev")
        'BRANCH_NAME'
        >>> config.branch_field_for(None)
        'branch'
    """
    env_config = self.environments.get(env) if env else None
    if env_config is not None and env_config.branch_field:
        return env_config.branch_field
    return self.branch_field


def project_name_from_job_key(env: str, job_key: str) -> Optional[str]:
    """
    从 job_key 推导原始项目名

    job_key 格式为 ``{env}_{project_name}``（项目名中的连字符已转下划线），
    因此推导需要先校验 ``{env}_`` 前缀，避免前缀不匹配时静默得到错误结果。

    Args:
        env: 环境名称
        job_key: Job 唯一标识

    Returns:
        推导出的项目名；env 为空、前缀不匹配或推导结果为空时返回 None

    Example:
        >>> project_name_from_job_key("dev", "dev_project_a")
        'project-a'
        >>> project_name_from_job_key("dev", "test_project_a") is None
        True
    """
    prefix = f"{env}_"
    if not env or not job_key.startswith(prefix):
        return None
    derived = job_key[len(prefix):].replace("_", "-")
    return derived or None


def env_from_job_key(job_key: str) -> str:
    """
    从 job_key 推导环境名

    job_key 格式为 ``{env}_{project_name}``，因此第一个下划线之前即环境名。
    与 project_name_from_job_key 一起把 job_key 的格式知识收敛在本模块。

    Args:
        job_key: Job 唯一标识

    Returns:
        环境名；job_key 中没有下划线时返回空字符串

    Example:
        >>> env_from_job_key("dev_project_a")
        'dev'
        >>> env_from_job_key("noseparator")
        ''
    """
    if "_" not in job_key:
        return ""
    return job_key.split("_", 1)[0]


def _get_jobs(
    self: Config,
    env: Optional[str] = None,
    jobs: Optional[list[str]] = None,
) -> list[Job]:
    """
    获取要构建的 Job 列表

    参数合并规则: 项目 params > 环境 params（简单的 dict update）
    分支派生: 从 params 中根据 branch_field 提取

    Args:
        env: 按环境过滤
        jobs: 按项目过滤，格式 ["env:project"] 或 ["project"]

    Returns:
        Job 列表
    """
    result = []

    for env_name, env_config in self.environments.items():
        if env and env != env_name:
            continue

        env_branch_field = self.branch_field_for(env_name)

        for project in env_config.projects:

            job_key = f"{env_name}_{project.name.replace('-', '_')}"

            if jobs and not _match_job_filter(job_key, project, env_name, jobs):
                continue

            # 参数合并: 项目 params > 环境 params
            merged_params = {}
            merged_params.update(env_config.params)
            merged_params.update(project.params)

            effective_branch = merged_params.get(env_branch_field, "")

            result.append(
                Job(
                    key=job_key,
                    path=project.path or project.name,
                    branch=effective_branch,
                    params=merged_params,
                    env=env_name,
                    project_name=project.name,
                )
            )

    return result


def _match_job_filter(
    job_key: str, project: Project, env_name: str, jobs: list[str]
) -> bool:
    """检查 job 是否匹配过滤条件"""
    for job_spec in jobs:
        if ":" in job_spec:
            spec_env, spec_proj = job_spec.split(":", 1)
            if spec_env == env_name and spec_proj == project.name:
                return True
        elif job_spec == project.name:
            return True
    return False


def _list_environments(self: Config) -> list[tuple[str, str]]:
    """列出所有环境名称和描述"""
    return [(name, env.description) for name, env in self.environments.items()]


def _list_projects(
    self: Config, env: Optional[str] = None
) -> list[tuple[str, str, str]]:
    """列出项目，返回 (env, name, path) 元组列表"""
    result = []
    for env_name, env_config in self.environments.items():
        if env and env != env_name:
            continue
        for project in env_config.projects:
            result.append((env_name, project.name, project.path or project.name))
    return result


def _create_job_from_record(self: Config, record: BuildRecord) -> Optional[Job]:
    """从历史记录创建 Job（用于重建功能）"""
    env_config = self.environments.get(record.env)
    if not env_config:
        return None

    project_name = record.project_name
    if not project_name:
        project_name = project_name_from_job_key(record.env, record.job_key)
        if not project_name:
            return None

    for project in env_config.projects:
        if project.name == project_name:
            job_key = f"{record.env}_{project.name.replace('-', '_')}"

            if record.params:
                merged_params = dict(record.params)
            else:
                merged_params = {}
                merged_params.update(env_config.params)
                merged_params.update(project.params)

            env_branch_field = self.branch_field_for(record.env)

            effective_branch = merged_params.get(env_branch_field, record.branch or "")


            return Job(
                key=job_key,
                path=project.path or project.name,
                branch=effective_branch,
                params=merged_params,
                env=record.env,
                project_name=project.name,
            )

    return None


# 附加业务方法到 Config 类
Config.get_jobs = _get_jobs
Config.list_environments = _list_environments
Config.list_projects = _list_projects
Config.create_job_from_record = _create_job_from_record
Config.branch_field_for = _branch_field_for
