# jenkins_config/config.py
"""
配置模块 - 负责配置文件的加载、解析和 Job 管理

这个模块是整个项目的配置中心，主要功能：
1. 定义配置数据结构（使用 dataclass）
2. 从 JSON 文件加载配置
3. 解析和合并构建参数
4. 根据环境和项目过滤获取 Job 列表

配置文件结构示例（jenkins-config.json）：
{
    "server": {"url": "http://jenkins.example.com", "token": "xxx"},
    "build": {"mode": "parallel", "poll_interval": 10},
    "environments": {
        "dev": {
            "default_branch": "develop",
            "params": "skip_tests=false",
            "projects": [{"name": "project-a", "branch": "feature"}]
        }
    }
}
"""

from __future__ import annotations  # 支持类型注解中的前向引用
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs
from typing import Optional


# ============================================================================
# 数据类定义（使用 @dataclass 装饰器自动生成 __init__、__repr__ 等方法）
# ============================================================================

@dataclass
class ServerConfig:
    """
    Jenkins 服务器配置
    
    Attributes:
        url: Jenkins 服务器地址，如 "http://jenkins.example.com:8080"
        token: API Token，用于认证（在 Jenkins 用户设置中生成）
    """
    url: str
    token: str


@dataclass
class BuildConfig:
    """
    构建行为配置
    
    Attributes:
        mode: 构建模式，"parallel"（并行）或 "sequential"（顺序）
        poll_interval: 轮询间隔（秒），用于检查构建状态
        build_timeout: 构建超时时间（秒）
        curl_timeout: HTTP 请求超时时间（秒）
        log_dir: 日志文件存储目录
    """
    mode: str = "parallel"           # 默认并行构建
    poll_interval: int = 10          # 默认每 10 秒轮询一次
    build_timeout: int = 3600        # 默认 1 小时超时
    curl_timeout: int = 30           # 默认 30 秒 HTTP 超时
    log_dir: str = "./jenkins_logs"  # 默认日志目录


@dataclass
class Project:
    """
    项目配置
    
    表示配置文件中 environments.xxx.projects 列表中的一个项目
    
    Attributes:
        name: 项目名称，对应 Jenkins Job 名称
        path: Jenkins Job 路径（默认与 name 相同，支持文件夹路径）
        branch: 构建分支（为空则使用环境默认分支）
        params: 项目特定参数（字典形式）
    """
    name: str
    path: str = ""                                    # 默认为空，后续会用 name 填充
    branch: str = ""                                  # 默认为空，表示使用环境默认分支
    params: dict = field(default_factory=dict)        # 默认空字典


@dataclass
class Environment:
    """
    环境配置
    
    表示一个完整的部署环境（如 dev、test、prod）
    
    Attributes:
        name: 环境名称
        default_branch: 该环境的默认分支
        params: 环境级别的参数（会被项目参数覆盖）
        projects: 该环境下的项目列表
    """
    name: str
    default_branch: str = "main"                      # 默认分支为 main
    params: dict = field(default_factory=dict)        # 环境级参数
    projects: list[Project] = field(default_factory=list)  # 项目列表


@dataclass
class Job:
    """
    构建任务
    
    这是实际执行构建时使用的数据结构，由 Config.get_jobs() 生成。
    Job 是 Project 和 Environment 的组合，包含所有合并后的参数。
    
    Attributes:
        key: Job 唯一标识，格式为 "env_project_name"（如 dev_pms_biz_plan_web）
        path: Jenkins Job 路径
        branch: 构建分支
        params: 合并后的参数（项目参数 > 环境参数 > 默认值）
        env: 所属环境
    """
    key: str
    path: str
    branch: str
    params: dict
    env: str


@dataclass
class Config:
    """
    主配置类
    
    这是配置模块的核心类，负责加载和管理所有配置
    
    Attributes:
        server: Jenkins 服务器配置
        build: 构建行为配置
        environments: 所有环境的配置（按环境名索引）
    """
    server: ServerConfig
    build: BuildConfig = field(default_factory=BuildConfig)
    environments: dict[str, Environment] = field(default_factory=dict)
    
    # ========================================================================
    # 类方法：加载配置
    # ========================================================================
    
    @classmethod
    def load(cls, config_path: str) -> Config:
        """
        从 JSON 文件加载配置
        
        这是配置的入口方法，读取 JSON 文件并构建完整的配置对象
        
        Args:
            config_path: 配置文件路径
        
        Returns:
            Config 对象
        
        Raises:
            FileNotFoundError: 配置文件不存在
            json.JSONDecodeError: JSON 格式错误
        
        Example:
            >>> config = Config.load("jenkins-config.json")
            >>> print(config.server.url)
            http://jenkins.example.com:8080
        """
        # 1. 检查文件是否存在
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        # 2. 读取并解析 JSON
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        
        # 3. 构建 ServerConfig（必需）
        server = ServerConfig(
            url=data["server"]["url"],
            token=data["server"]["token"]
        )
        
        # 4. 构建 BuildConfig（可选，有默认值）
        build_data = data.get("build", {})  # 如果没有 build 配置，使用空字典
        build = BuildConfig(
            mode=build_data.get("mode", "parallel"),
            poll_interval=build_data.get("poll_interval", 10),
            build_timeout=build_data.get("build_timeout", 3600),
            curl_timeout=build_data.get("curl_timeout", 30),
            log_dir=build_data.get("log_dir", "./jenkins_logs")
        )
        
        # 5. 构建所有环境配置
        environments = {}
        for env_name, env_data in data.get("environments", {}).items():
            # 解析环境级别的参数（格式如 "skip_tests=false&debug=true"）
            env_params = cls._parse_params(env_data.get("params", ""))
            
            # 构建该环境下的所有项目
            projects = []
            for proj_data in env_data.get("projects", []):
                # 解析项目级别的参数
                proj_params = cls._parse_params(proj_data.get("params", ""))
                projects.append(Project(
                    name=proj_data["name"],
                    # path 默认使用 name，但可以显式指定（用于文件夹结构）
                    path=proj_data.get("path", proj_data["name"]),
                    branch=proj_data.get("branch", ""),
                    params=proj_params
                ))
            
            environments[env_name] = Environment(
                name=env_name,
                default_branch=env_data.get("default_branch", "main"),
                params=env_params,
                projects=projects
            )
        
        # 6. 返回完整的配置对象
        return cls(server=server, build=build, environments=environments)
    
    # ========================================================================
    # 私有方法：参数解析
    # ========================================================================
    
    @staticmethod
    def _parse_params(params_str: str) -> dict:
        """
        解析参数字符串为字典
        
        将 URL 查询字符串格式（如 "key1=value1&key2=value2"）转换为字典
        
        Args:
            params_str: 参数字符串
        
        Returns:
            参数字典
        
        Example:
            >>> Config._parse_params("skip_tests=false&debug=true")
            {'skip_tests': 'false', 'debug': 'true'}
            >>> Config._parse_params("")
            {}
        """
        if not params_str:
            return {}
        
        result = {}
        # 按 & 分割键值对
        for pair in params_str.split("&"):
            if "=" in pair:
                # split("=", 1) 只在第一个 = 处分割，支持值中包含 =
                k, v = pair.split("=", 1)
                result[k.strip()] = v.strip()
        return result
    
    # ========================================================================
    # 公共方法：获取 Job
    # ========================================================================
    
    def get_jobs(self, env: Optional[str] = None, jobs: Optional[list[str]] = None) -> list[Job]:
        """
        获取要构建的 Job 列表
        
        这是构建流程的核心方法，根据环境和项目过滤条件生成 Job 列表。
        同时处理参数合并：项目参数 > 环境参数 > 默认值
        
        Args:
            env: 指定环境，为 None 时获取所有环境的 Job
            jobs: 指定项目列表，格式为 ["env:project", ...] 或 ["project", ...]
                  为 None 时获取所有项目
        
        Returns:
            Job 列表
        
        Example:
            # 获取 dev 环境的所有项目
            >>> jobs = config.get_jobs(env="dev")
            
            # 获取特定项目（使用 env:project 格式）
            >>> jobs = config.get_jobs(jobs=["dev:project-a", "test:project-b"])
            
            # 获取特定项目（仅项目名，匹配所有环境）
            >>> jobs = config.get_jobs(jobs=["project-a"])
        """
        result = []
        
        # 遍历所有环境
        for env_name, env_config in self.environments.items():
            # 如果指定了环境，跳过不匹配的
            if env and env != env_name:
                continue
            
            # 遍历该环境下的所有项目
            for project in env_config.projects:
                # 生成 Job key：环境名_项目名（- 替换为 _）
                job_key = f"{env_name}_{project.name.replace('-', '_')}"
                
                # 如果指定了 jobs 列表，进行过滤匹配
                if jobs:
                    matched = False
                    for job_spec in jobs:
                        # 支持两种格式：
                        # 1. "env:project" - 精确匹配环境和项目
                        # 2. "project" - 仅匹配项目名（任意环境）
                        if ":" in job_spec:
                            spec_env, spec_proj = job_spec.split(":", 1)
                            if spec_env == env_name and spec_proj == project.name:
                                matched = True
                                break
                        elif job_spec == project.name:
                            matched = True
                            break
                    
                    if not matched:
                        continue
                
                # ------------------------------------------------------------
                # 参数合并（优先级从低到高）：
                # 1. 默认分支
                # 2. 环境参数
                # 3. 项目参数
                # ------------------------------------------------------------
                merged_params = {"branch": project.branch or env_config.default_branch}
                merged_params.update(env_config.params)   # 环境参数覆盖默认值
                merged_params.update(project.params)      # 项目参数覆盖环境参数
                
                # 创建 Job 对象
                result.append(Job(
                    key=job_key,
                    path=project.path or project.name,
                    branch=project.branch or env_config.default_branch,
                    params=merged_params,
                    env=env_name
                ))
        
        return result
    
    # ========================================================================
    # 公共方法：列表查询
    # ========================================================================
    
    def list_environments(self) -> list[str]:
        """
        列出所有环境名称
        
        Returns:
            环境名称列表
        
        Example:
            >>> config.list_environments()
            ['dev', 'test', 'prod']
        """
        return list(self.environments.keys())
    
    def list_projects(self, env: Optional[str] = None) -> list[tuple[str, str, str]]:
        """
        列出项目
        
        Args:
            env: 指定环境，为 None 时列出所有环境的项目
        
        Returns:
            元组列表，每个元组格式为 (env, name, path)
        
        Example:
            >>> config.list_projects("dev")
            [('dev', 'project-a', 'project-a'), ('dev', 'project-b', 'project-b')]
            
            >>> config.list_projects()  # 所有环境
            [('dev', 'project-a', 'project-a'), ('test', 'project-a', 'project-a')]
        """
        result = []
        for env_name, env_config in self.environments.items():
            if env and env != env_name:
                continue
            for project in env_config.projects:
                result.append((env_name, project.name, project.path or project.name))
        return result