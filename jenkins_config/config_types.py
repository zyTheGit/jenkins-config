# jenkins_config/config_types.py
"""
配置数据类型定义

本模块只包含纯 dataclass 定义，不包含任何 I/O 或业务逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .history import BuildRecord


@dataclass
class ServerConfig:
    """
    Jenkins 服务器配置

    Attributes:
        url: Jenkins 服务器地址
        username: Jenkins 用户名（默认: admin）
        token: API Token
    """

    url: str
    username: str = "admin"
    token: str = ""


@dataclass
class BuildConfig:
    """
    构建行为配置

    Attributes:
        mode: 构建模式，"parallel" 或 "sequential"
        poll_interval: 轮询间隔（秒）
        build_timeout: 构建超时（秒）
        curl_timeout: HTTP 请求超时（秒）
        log_dir: 日志目录
        log_retention_days: 日志保留天数
    """

    mode: str = "parallel"
    poll_interval: int = 10
    build_timeout: int = 3600
    curl_timeout: int = 30
    log_dir: str = "./jenkins_logs"
    log_retention_days: int = 3


@dataclass
class Project:
    """
    项目配置

    所有 Jenkins 构建参数都放在 params 字典中，无需硬编码字段。
    新增插件参数只需在 params 中添加键值对，无需修改代码。

    Attributes:
        name: 项目名称，对应 Jenkins Job 名称
        path: Jenkins Job 路径（默认与 name 相同）
        params: 项目参数（字典，项目 > 环境 > 默认值）
    """

    name: str
    path: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Environment:
    """
    环境配置

    Attributes:
        name: 环境名称
        description: 环境描述（可选）
        branch_field: 覆盖全局 branch_field（可选）
        params: 环境级别参数（会被项目参数覆盖）
        projects: 项目列表
    """

    name: str
    description: str = ""
    branch_field: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    projects: list[Project] = field(default_factory=list)


@dataclass
class Job:
    """
    构建任务

    由 Config.get_jobs() 生成，是 Project 和 Environment 的组合。

    Attributes:
        key: 唯一标识，格式 "env_project_name"
        path: Jenkins Job 路径
        branch: 构建分支（从 params 根据 branch_field 派生）
        params: 合并后的参数（项目 params > 环境 params）
        env: 所属环境
        project_name: 原始项目名称
    """

    key: str
    path: str
    branch: str
    params: dict
    env: str
    project_name: str = ""


@dataclass
class Config:
    """
    主配置类

    Attributes:
        server: Jenkins 服务器配置
        build: 构建行为配置
        branch_field: CLI -b/--branch 使用的参数名（默认: branch）
        environments: 环境配置字典（按环境名索引）
    """

    server: ServerConfig = field(default_factory=lambda: ServerConfig("", ""))
    build: BuildConfig = field(default_factory=BuildConfig)
    branch_field: str = "branch"
    environments: dict[str, Environment] = field(default_factory=dict)

    # 以下方法在 config.py 中通过猴子补丁添加，此处仅为类型检查器提供签名
    if TYPE_CHECKING:
        @staticmethod
        def show_template() -> None: ...

        @staticmethod
        def generate_template() -> dict[str, Any]: ...

        @classmethod
        def load(cls, path: str) -> Config: ...

        def save(self, path: str) -> None: ...

        def to_dict(self) -> dict[str, Any]: ...

        def get_jobs(
            self,
            env: Optional[str] = None,
            jobs: Optional[list[str]] = None,
        ) -> list[Job]: ...

        def list_environments(self) -> list[tuple[str, str]]: ...

        def list_projects(
            self, env: Optional[str] = None
        ) -> list[tuple[str, str, str]]: ...

        def create_job_from_record(
            self, record: BuildRecord
        ) -> Optional[Job]: ...
