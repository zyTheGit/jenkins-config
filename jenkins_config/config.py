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

        env_branch_field = env_config.branch_field or self.branch_field

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
        project_name = record.job_key.replace(f"{record.env}_", "").replace("_", "-")

    for project in env_config.projects:
        if project.name == project_name:
            job_key = f"{record.env}_{project.name.replace('-', '_')}"

            if record.params:
                merged_params = dict(record.params)
            else:
                merged_params = {}
                merged_params.update(env_config.params)
                merged_params.update(project.params)

            env_branch_field = env_config.branch_field or self.branch_field
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
