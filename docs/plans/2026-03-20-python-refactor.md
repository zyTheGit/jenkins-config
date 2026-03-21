# Jenkins Auto Build Python 重构实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 shell 脚本重构为模块化 Python 项目，移除外部依赖，添加历史记录功能。

**Architecture:** 6 个模块：config（配置）、jenkins（API）、builder（构建编排）、history（历史记录）、utils（工具）、cli（入口）。使用 requests 替代 curl/jq/node。

**Tech Stack:** Python 3.10+, requests, argparse, concurrent.futures, dataclasses

---

## Task 1: 项目初始化

**Files:**
- Create: `pyproject.toml`
- Create: `jenkins_config/__init__.py`
- Create: `data/.gitkeep`

**Step 1: 创建 pyproject.toml**

```toml
[project]
name = "jenkins-config"
version = "1.0.0"
description = "Jenkins 自动构建工具"
requires-python = ">=3.10"
dependencies = [
    "requests>=2.28.0",
]

[project.scripts]
jenkins-build = "jenkins_config.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["jenkins_config"]
```

**Step 2: 创建包目录**

```bash
mkdir -p jenkins_config data
touch jenkins_config/__init__.py data/.gitkeep
```

**Step 3: 验证项目结构**

Run: `ls -la jenkins_config/`
Expected: `__init__.py` exists

**Step 4: Commit**

```bash
git add pyproject.toml jenkins_config/ data/
git commit -m "chore: init python project structure"
```

---

## Task 2: 工具模块 (utils.py)

**Files:**
- Create: `jenkins_config/utils.py`
- Test: `tests/test_utils.py`

**Step 1: 创建测试目录**

```bash
mkdir -p tests
```

**Step 2: 写测试**

```python
# tests/test_utils.py
import sys
from io import StringIO
from jenkins_config.utils import format_duration, print_sep

def test_format_duration():
    assert format_duration(0) == "0分0秒"
    assert format_duration(65) == "1分5秒"
    assert format_duration(120) == "2分0秒"

def test_print_sep(capsys):
    print_sep("-")
    captured = capsys.readouterr()
    assert "-" * 80 in captured.err
```

**Step 3: 运行测试验证失败**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_utils.py -v`
Expected: FAIL (module not found)

**Step 4: 实现工具函数**

```python
# jenkins_config/utils.py
import sys

def log_info(msg: str):
    print(f"\033[0;36m[INFO] {msg}\033[0m", file=sys.stderr)

def log_success(msg: str):
    print(f"\033[0;32m[SUCCESS] {msg}\033[0m", file=sys.stderr)

def log_error(msg: str):
    print(f"\033[0;31m[ERROR] {msg}\033[0m", file=sys.stderr)

def log_warn(msg: str):
    print(f"\033[0;33m[WARN] {msg}\033[0m", file=sys.stderr)

def print_sep(char: str = "="):
    print(char * 80, file=sys.stderr)

def print_header(title: str):
    print_sep()
    print(f"\033[0;36m{title}\033[0m", file=sys.stderr)
    print_sep()

def format_duration(seconds: int) -> str:
    mins, secs = divmod(seconds, 60)
    return f"{mins}分{secs}秒"
```

**Step 5: 运行测试验证通过**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_utils.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add jenkins_config/utils.py tests/test_utils.py
git commit -m "feat: add utils module with logging and duration formatting"
```

---

## Task 3: 配置模块 (config.py)

**Files:**
- Create: `jenkins_config/config.py`
- Test: `tests/test_config.py`

**Step 1: 写测试**

```python
# tests/test_config.py
import pytest
from jenkins_config.config import Config, Job

def test_load_config(tmp_path):
    config_file = tmp_path / "test-config.json"
    config_file.write_text('''
    {
      "server": {"url": "http://localhost:8080", "token": "test-token"},
      "build": {"mode": "parallel", "poll_interval": 5},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "projects": [{"name": "test-project", "branch": "feature"}]
        }
      }
    }
    ''')
    
    config = Config.load(str(config_file))
    assert config.server.url == "http://localhost:8080"
    assert config.server.token == "test-token"
    assert config.build.mode == "parallel"
    assert config.build.poll_interval == 5

def test_get_jobs(tmp_path):
    config_file = tmp_path / "test-config.json"
    config_file.write_text('''
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {
          "default_branch": "develop",
          "params": "skip_tests=false",
          "projects": [
            {"name": "project-a", "branch": "feature"},
            {"name": "project-b"}
          ]
        }
      }
    }
    ''')
    
    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")
    
    assert len(jobs) == 2
    assert jobs[0].key == "dev_project_a"
    assert jobs[0].branch == "feature"
    assert jobs[1].branch == "develop"  # 使用环境默认分支

def test_get_jobs_with_filter(tmp_path):
    config_file = tmp_path / "test-config.json"
    config_file.write_text('''
    {
      "server": {"url": "http://localhost:8080", "token": "token"},
      "environments": {
        "dev": {"projects": [{"name": "project-a"}]},
        "test": {"projects": [{"name": "project-b"}]}
      }
    }
    ''')
    
    config = Config.load(str(config_file))
    jobs = config.get_jobs(jobs=["dev:project-a"])
    
    assert len(jobs) == 1
    assert jobs[0].env == "dev"
```

**Step 2: 运行测试验证失败**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_config.py -v`
Expected: FAIL

**Step 3: 实现配置模块**

```python
# jenkins_config/config.py
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs
from typing import Optional

@dataclass
class ServerConfig:
    url: str
    token: str

@dataclass
class BuildConfig:
    mode: str = "parallel"
    poll_interval: int = 10
    build_timeout: int = 3600
    curl_timeout: int = 30
    log_dir: str = "./jenkins_logs"

@dataclass
class Project:
    name: str
    path: str = ""
    branch: str = ""
    params: dict = field(default_factory=dict)

@dataclass
class Environment:
    name: str
    default_branch: str = "main"
    params: dict = field(default_factory=dict)
    projects: list[Project] = field(default_factory=list)

@dataclass
class Job:
    key: str
    path: str
    branch: str
    params: dict
    env: str

@dataclass
class Config:
    server: ServerConfig
    build: BuildConfig = field(default_factory=BuildConfig)
    environments: dict[str, Environment] = field(default_factory=dict)
    
    @classmethod
    def load(cls, config_path: str) -> Config:
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        
        server = ServerConfig(
            url=data["server"]["url"],
            token=data["server"]["token"]
        )
        
        build_data = data.get("build", {})
        build = BuildConfig(
            mode=build_data.get("mode", "parallel"),
            poll_interval=build_data.get("poll_interval", 10),
            build_timeout=build_data.get("build_timeout", 3600),
            curl_timeout=build_data.get("curl_timeout", 30),
            log_dir=build_data.get("log_dir", "./jenkins_logs")
        )
        
        environments = {}
        for env_name, env_data in data.get("environments", {}).items():
            env_params = cls._parse_params(env_data.get("params", ""))
            projects = []
            for proj_data in env_data.get("projects", []):
                proj_params = cls._parse_params(proj_data.get("params", ""))
                projects.append(Project(
                    name=proj_data["name"],
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
        
        return cls(server=server, build=build, environments=environments)
    
    @staticmethod
    def _parse_params(params_str: str) -> dict:
        if not params_str:
            return {}
        result = {}
        for pair in params_str.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                result[k.strip()] = v.strip()
        return result
    
    def get_jobs(self, env: Optional[str] = None, jobs: Optional[list[str]] = None) -> list[Job]:
        result = []
        
        for env_name, env_config in self.environments.items():
            if env and env != env_name:
                continue
            
            for project in env_config.projects:
                job_key = f"{env_name}_{project.name.replace('-', '_')}"
                
                # 如果指定了 jobs 列表，过滤匹配的
                if jobs:
                    matched = False
                    for job_spec in jobs:
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
                
                # 合并参数
                merged_params = {"branch": project.branch or env_config.default_branch}
                merged_params.update(env_config.params)
                merged_params.update(project.params)
                
                result.append(Job(
                    key=job_key,
                    path=project.path or project.name,
                    branch=project.branch or env_config.default_branch,
                    params=merged_params,
                    env=env_name
                ))
        
        return result
    
    def list_environments(self) -> list[str]:
        return list(self.environments.keys())
    
    def list_projects(self, env: Optional[str] = None) -> list[tuple[str, str, str]]:
        """返回 [(env, name, path), ...]"""
        result = []
        for env_name, env_config in self.environments.items():
            if env and env != env_name:
                continue
            for project in env_config.projects:
                result.append((env_name, project.name, project.path or project.name))
        return result
```

**Step 4: 运行测试验证通过**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_config.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add jenkins_config/config.py tests/test_config.py
git commit -m "feat: add config module with JSON loading and job filtering"
```

---

## Task 4: Jenkins API 客户端 (jenkins.py)

**Files:**
- Create: `jenkins_config/jenkins.py`
- Test: `tests/test_jenkins.py`

**Step 1: 写测试**

```python
# tests/test_jenkins.py
import pytest
from unittest.mock import Mock, patch, MagicMock
from jenkins_config.jenkins import JenkinsClient, BuildStatus

@pytest.fixture
def client():
    return JenkinsClient("http://localhost:8080", "test-token")

def test_get_crumb(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb",
            "crumbRequestField": "Jenkins-Crumb"
        }
        result = client._get_crumb()
        assert result == ("Jenkins-Crumb", "test-crumb")

def test_trigger_build_success(client):
    with patch.object(client.session, "post") as mock_post:
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response
        
        result = client.trigger_build("test-job", {"branch": "main"})
        assert result == "http://localhost:8080/queue/item/123/"

def test_get_build_number(client):
    with patch.object(client.session, "get") as mock_get:
        # 第一次返回空，第二次返回编号
        mock_get.return_value.json.side_effect = [
            {"cancelled": False, "executable": None},
            {"cancelled": False, "executable": {"number": 456}}
        ]
        result = client.get_build_number("http://localhost:8080/queue/item/123/", timeout=2)
        assert result == 456

def test_get_build_status(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": "SUCCESS",
            "duration": 60000
        }
        info = client.get_build_status("test-job", 123)
        assert info.status == BuildStatus.SUCCESS
        assert info.duration == 60
```

**Step 2: 运行测试验证失败**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_jenkins.py -v`
Expected: FAIL

**Step 3: 实现 Jenkins 客户端**

```python
# jenkins_config/jenkins.py
from __future__ import annotations
import time
from enum import Enum
from dataclasses import dataclass
from urllib.parse import quote
from typing import Optional
import requests


class BuildStatus(Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ABORTED = "ABORTED"
    BUILDING = "BUILDING"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"


@dataclass
class BuildInfo:
    number: int
    status: BuildStatus
    result: Optional[str]
    duration: int  # 秒


class JenkinsClient:
    def __init__(self, url: str, token: str, timeout: int = 30):
        self.session = requests.Session()
        self.session.auth = ("admin", token)
        self.base_url = url.rstrip("/")
        self.timeout = timeout
    
    def _get_crumb(self) -> Optional[tuple[str, str]]:
        """获取 CSRF Token"""
        try:
            resp = self.session.get(
                f"{self.base_url}/crumbIssuer/api/json",
                timeout=self.timeout
            )
            if resp.ok:
                data = resp.json()
                return (data.get("crumbRequestField", "Jenkins-Crumb"), 
                        data.get("crumb"))
        except Exception:
            pass
        return None
    
    def trigger_build(self, job_path: str, params: dict) -> Optional[str]:
        """触发构建，返回 queue_url"""
        encoded_path = quote(job_path, safe="")
        url = f"{self.base_url}/job/{encoded_path}/buildWithParameters"
        
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        crumb = self._get_crumb()
        if crumb:
            headers[crumb[0]] = crumb[1]
        
        try:
            resp = self.session.post(
                url,
                data=params,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False
            )
            if resp.status_code == 201:
                return resp.headers.get("Location")
        except Exception:
            pass
        return None
    
    def get_build_number(self, queue_url: str, timeout: int = 30) -> Optional[int]:
        """从队列获取构建编号"""
        for _ in range(timeout):
            try:
                resp = self.session.get(
                    f"{queue_url.rstrip('/')}/api/json",
                    timeout=self.timeout
                )
                if resp.ok:
                    data = resp.json()
                    if data.get("cancelled"):
                        return None
                    executable = data.get("executable")
                    if executable and executable.get("number"):
                        return executable["number"]
            except Exception:
                pass
            time.sleep(1)
        return None
    
    def get_build_status(self, job_path: str, build_num: int) -> BuildInfo:
        """获取构建状态"""
        encoded_path = quote(job_path, safe="")
        url = f"{self.base_url}/job/{encoded_path}/{build_num}/api/json"
        
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.ok:
                data = resp.json()
                result = data.get("result")
                duration_ms = data.get("duration", 0)
                
                if result == "SUCCESS":
                    status = BuildStatus.SUCCESS
                elif result == "FAILURE":
                    status = BuildStatus.FAILURE
                elif result == "ABORTED":
                    status = BuildStatus.ABORTED
                else:
                    status = BuildStatus.BUILDING
                
                return BuildInfo(
                    number=build_num,
                    status=status,
                    result=result,
                    duration=duration_ms // 1000
                )
        except Exception:
            pass
        
        return BuildInfo(number=build_num, status=BuildStatus.BUILDING, result=None, duration=0)
    
    def get_build_log(self, job_path: str, build_num: int) -> str:
        """获取构建日志"""
        encoded_path = quote(job_path, safe="")
        url = f"{self.base_url}/job/{encoded_path}/{build_num}/consoleText"
        
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.ok:
                return resp.text
        except Exception:
            pass
        return ""
```

**Step 4: 运行测试验证通过**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_jenkins.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add jenkins_config/jenkins.py tests/test_jenkins.py
git commit -m "feat: add Jenkins API client with build trigger and status polling"
```

---

## Task 5: 构建编排模块 (builder.py)

**Files:**
- Create: `jenkins_config/builder.py`
- Test: `tests/test_builder.py`

**Step 1: 写测试**

```python
# tests/test_builder.py
import pytest
from unittest.mock import Mock, patch, MagicMock
from jenkins_config.builder import Builder, BuildResult
from jenkins_config.jenkins import BuildStatus, JenkinsClient
from jenkins_config.config import Config, Job, BuildConfig

@pytest.fixture
def mock_client():
    client = Mock(spec=JenkinsClient)
    client.trigger_build.return_value = "http://localhost/queue/item/1/"
    client.get_build_number.return_value = 123
    client.get_build_status.return_value = Mock(
        status=BuildStatus.SUCCESS,
        duration=60
    )
    client.get_build_log.return_value = "Build log content"
    return client

@pytest.fixture
def builder(mock_client):
    config = Mock(spec=Config)
    config.build = BuildConfig(build_timeout=60, poll_interval=1)
    return Builder(mock_client, config)

def test_build_single_success(builder, tmp_path):
    job = Job(key="dev_test", path="test-job", branch="main", params={}, env="dev")
    result = builder._build_single(job, str(tmp_path))
    
    assert result.status == BuildStatus.SUCCESS
    assert result.build_num == 123
    assert result.error is None

def test_build_sequential(builder, tmp_path):
    jobs = [
        Job(key="dev_a", path="job-a", branch="main", params={}, env="dev"),
        Job(key="dev_b", path="job-b", branch="main", params={}, env="dev")
    ]
    results = builder.build_sequential(jobs, str(tmp_path))
    
    assert len(results) == 2
    assert all(r.status == BuildStatus.SUCCESS for r in results)

def test_build_parallel(builder, tmp_path):
    jobs = [
        Job(key="dev_a", path="job-a", branch="main", params={}, env="dev"),
        Job(key="dev_b", path="job-b", branch="main", params={}, env="dev")
    ]
    results = builder.build_parallel(jobs, str(tmp_path))
    
    assert len(results) == 2
```

**Step 2: 运行测试验证失败**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_builder.py -v`
Expected: FAIL

**Step 3: 实现构建模块**

```python
# jenkins_config/builder.py
from __future__ import annotations
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from .jenkins import BuildStatus
from .utils import log_info, log_success, log_error, log_warn

if TYPE_CHECKING:
    from .jenkins import JenkinsClient
    from .config import Config, Job


@dataclass
class BuildResult:
    job_key: str
    build_num: int
    status: BuildStatus
    duration: int  # 秒
    log_file: str
    error: str | None = None


class Builder:
    def __init__(self, client: JenkinsClient, config: Config):
        self.client = client
        self.config = config
    
    def build_sequential(self, jobs: list[Job], log_dir: str) -> list[BuildResult]:
        """顺序构建"""
        results = []
        for job in jobs:
            result = self._build_single(job, log_dir)
            results.append(result)
        return results
    
    def build_parallel(self, jobs: list[Job], log_dir: str) -> list[BuildResult]:
        """并行构建"""
        results = []
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            future_to_job = {
                executor.submit(self._build_single, job, log_dir): job
                for job in jobs
            }
            for future in as_completed(future_to_job):
                result = future.result()
                results.append(result)
        return results
    
    def _build_single(self, job: Job, log_dir: str) -> BuildResult:
        """单个构建流程"""
        log_info(f"正在触发构建：{job.key} ({job.path})")
        
        # 1. 触发构建
        queue_url = self.client.trigger_build(job.path, job.params)
        if not queue_url:
            log_error(f"触发构建失败：{job.key}")
            return BuildResult(
                job_key=job.key,
                build_num=0,
                status=BuildStatus.FAILURE,
                duration=0,
                log_file="",
                error="触发构建失败"
            )
        
        log_info(f"构建已排队，等待分配编号：{job.key}")
        
        # 2. 获取构建编号
        build_num = self.client.get_build_number(queue_url, timeout=30)
        if not build_num:
            log_error(f"获取构建编号超时：{job.key}")
            return BuildResult(
                job_key=job.key,
                build_num=0,
                status=BuildStatus.TIMEOUT,
                duration=0,
                log_file="",
                error="获取构建编号超时"
            )
        
        log_success(f"构建已触发，编号：#{build_num}")
        
        # 3. 等待构建完成
        status = self._wait_for_build(job, build_num)
        
        # 4. 获取日志
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_file = f"{log_dir}/{job.key}_#{build_num}.log"
        log_content = self.client.get_build_log(job.path, build_num)
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(log_content)
        log_info(f"日志已保存：{log_file}")
        
        duration = self.client.get_build_status(job.path, build_num).duration
        
        if status == BuildStatus.SUCCESS:
            log_success(f"构建完成：{job.key} (#{build_num}) - 成功")
        elif status == BuildStatus.FAILURE:
            log_error(f"构建失败：{job.key} (#{build_num}) - 失败")
        else:
            log_warn(f"构建中止：{job.key} (#{build_num})")
        
        return BuildResult(
            job_key=job.key,
            build_num=build_num,
            status=status,
            duration=duration,
            log_file=log_file
        )
    
    def _wait_for_build(self, job: Job, build_num: int) -> BuildStatus:
        """等待构建完成"""
        start = time.time()
        timeout = self.config.build.build_timeout
        poll_interval = self.config.build.poll_interval
        
        while True:
            elapsed = time.time() - start
            if elapsed >= timeout:
                return BuildStatus.TIMEOUT
            
            info = self.client.get_build_status(job.path, build_num)
            
            if info.status in (BuildStatus.SUCCESS, BuildStatus.FAILURE, BuildStatus.ABORTED):
                return info.status
            
            mins, secs = divmod(int(elapsed), 60)
            log_info(f"监控中：{job.key} (#{build_num}) - 已运行 {mins}分{secs}秒")
            
            time.sleep(poll_interval)
```

**Step 4: 运行测试验证通过**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_builder.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add jenkins_config/builder.py tests/test_builder.py
git commit -m "feat: add builder module with sequential and parallel build support"
```

---

## Task 6: 历史记录模块 (history.py)

**Files:**
- Create: `jenkins_config/history.py`
- Test: `tests/test_history.py`

**Step 1: 写测试**

```python
# tests/test_history.py
import pytest
import json
from pathlib import Path
from jenkins_config.history import HistoryManager, BuildRecord
from jenkins_config.jenkins import BuildStatus

def test_add_record(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    
    record = BuildRecord(
        timestamp="2026-03-20T10:00:00",
        env="dev",
        job_key="dev_test",
        build_num=123,
        status="SUCCESS",
        duration=60,
        log_file="/tmp/test.log"
    )
    
    manager.add(record)
    
    # 验证文件写入
    data = json.loads(history_file.read_text())
    assert len(data["records"]) == 1
    assert data["records"][0]["job_key"] == "dev_test"

def test_list_records(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    
    # 添加多条记录
    for i in range(3):
        manager.add(BuildRecord(
            timestamp=f"2026-03-20T10:0{i}:00",
            env="dev" if i < 2 else "test",
            job_key=f"dev_test_{i}",
            build_num=100 + i,
            status="SUCCESS",
            duration=60,
            log_file=""
        ))
    
    # 查询所有
    all_records = manager.list()
    assert len(all_records) == 3
    
    # 按环境过滤
    dev_records = manager.list(env="dev")
    assert len(dev_records) == 2

def test_stats(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    
    manager.add(BuildRecord(
        timestamp="", env="", job_key="", build_num=1,
        status="SUCCESS", duration=60, log_file=""
    ))
    manager.add(BuildRecord(
        timestamp="", env="", job_key="", build_num=2,
        status="FAILURE", duration=30, log_file=""
    ))
    
    stats = manager.stats()
    assert stats["total"] == 2
    assert stats["success"] == 1
    assert stats["failure"] == 1
    assert stats["success_rate"] == 50.0

def test_max_records_limit(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.MAX_RECORDS = 5
    
    # 添加 10 条记录
    for i in range(10):
        manager.add(BuildRecord(
            timestamp=f"2026-03-20T10:00:{i:02d}",
            env="dev", job_key=f"job_{i}", build_num=i,
            status="SUCCESS", duration=60, log_file=""
        ))
    
    records = manager.list()
    assert len(records) == 5
    # 应该保留最新的 5 条
    assert records[0].job_key == "job_9"
```

**Step 2: 运行测试验证失败**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_history.py -v`
Expected: FAIL

**Step 3: 实现历史记录模块**

```python
# jenkins_config/history.py
from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class BuildRecord:
    timestamp: str
    env: str
    job_key: str
    build_num: int
    status: str
    duration: int
    log_file: str


class HistoryManager:
    MAX_RECORDS = 100
    
    def __init__(self, history_file: str = "data/build_history.json"):
        self.history_file = Path(history_file)
        self._ensure_file()
    
    def _ensure_file(self):
        if not self.history_file.exists():
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            self._write_records([])
    
    def _read_records(self) -> list[dict]:
        try:
            with open(self.history_file, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("records", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    def _write_records(self, records: list[dict]):
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump({"records": records}, f, ensure_ascii=False, indent=2)
    
    def add(self, record: BuildRecord):
        records = self._read_records()
        records.insert(0, asdict(record))  # 新记录在前
        records = records[:self.MAX_RECORDS]  # 限制数量
        self._write_records(records)
    
    def list(self, env: Optional[str] = None, limit: int = 20) -> list[BuildRecord]:
        records = self._read_records()
        
        if env:
            records = [r for r in records if r.get("env") == env]
        
        return [BuildRecord(**r) for r in records[:limit]]
    
    def stats(self) -> dict:
        records = self._read_records()
        total = len(records)
        success = sum(1 for r in records if r.get("status") == "SUCCESS")
        failure = sum(1 for r in records if r.get("status") in ("FAILURE", "TIMEOUT"))
        
        return {
            "total": total,
            "success": success,
            "failure": failure,
            "success_rate": round(success / total * 100, 1) if total > 0 else 0
        }
    
    def clear(self):
        self._write_records([])
```

**Step 4: 运行测试验证通过**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/test_history.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add jenkins_config/history.py tests/test_history.py
git commit -m "feat: add history module for build record persistence"
```

---

## Task 7: CLI 入口 (cli.py)

**Files:**
- Create: `jenkins_config/cli.py`

**Step 1: 实现 CLI 模块**

```python
# jenkins_config/cli.py
import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import Config
from .jenkins import JenkinsClient
from .builder import Builder, BuildResult
from .history import HistoryManager, BuildRecord
from .jenkins import BuildStatus
from .utils import (
    log_info, log_success, log_error, log_warn,
    print_sep, print_header, format_duration
)


def main():
    parser = argparse.ArgumentParser(description="Jenkins 自动构建工具")
    
    # 构建参数
    parser.add_argument("-m", "--mode", choices=["parallel", "sequential"],
                        default="parallel", help="构建模式")
    parser.add_argument("-e", "--env", help="构建指定环境")
    parser.add_argument("-j", "--jobs", help="构建指定项目 (格式: env:project,env:project)")
    parser.add_argument("-p", "--params", help="构建参数 (格式: key=value&key2=value2)")
    parser.add_argument("-c", "--config", default="jenkins-config.json",
                        help="配置文件路径")
    
    # 列表命令
    parser.add_argument("--list-envs", action="store_true", help="列出所有环境")
    parser.add_argument("--list-projects", metavar="ENV", nargs="?", const="",
                        help="列出项目（不指定 ENV 则列出所有）")
    
    # 历史命令
    parser.add_argument("--history", action="store_true", help="查看构建历史")
    parser.add_argument("--history-stats", action="store_true", help="查看历史统计")
    
    # 其他
    parser.add_argument("-d", "--debug", action="store_true", help="调试模式")
    
    args = parser.parse_args()
    
    # 确定配置文件路径
    script_dir = Path(__file__).parent.parent
    config_file = Path(args.config)
    if not config_file.is_absolute():
        config_file = script_dir / args.config
    
    # 列表命令
    if args.list_envs:
        list_environments(config_file)
        return
    
    if args.list_projects is not None:
        list_projects(config_file, args.list_projects)
        return
    
    # 历史命令
    if args.history:
        show_history(config_file, args.env)
        return
    
    if args.history_stats:
        show_history_stats(config_file)
        return
    
    # 构建流程
    run_build(config_file, args)


def list_environments(config_file: Path):
    config = Config.load(str(config_file))
    print_header("所有环境")
    for env in config.list_environments():
        print(f"  - {env}")


def list_projects(config_file: Path, env: str | None):
    config = Config.load(str(config_file))
    
    if env:
        print_header(f"环境 '{env}' 的项目")
        for e, name, path in config.list_projects(env):
            print(f"  - {name} ({path})")
    else:
        print_header("所有环境的项目")
        current_env = None
        for e, name, path in config.list_projects():
            if e != current_env:
                print(f"\n[{e}]")
                current_env = e
            print(f"  - {name} ({path})")


def show_history(config_file: Path, env: str | None):
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    records = manager.list(env=env, limit=20)
    
    print_header("构建历史")
    if not records:
        print("暂无记录")
        return
    
    for r in records:
        status_icon = "✓" if r.status == "SUCCESS" else "✗"
        print(f"  {status_icon} [{r.timestamp}] {r.job_key} #{r.build_num} - {r.status} ({format_duration(r.duration)})")


def show_history_stats(config_file: Path):
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    stats = manager.stats()
    
    print_header("历史统计")
    print(f"  总构建数: {stats['total']}")
    print(f"  成功数: {stats['success']}")
    print(f"  失败数: {stats['failure']}")
    print(f"  成功率: {stats['success_rate']}%")


def run_build(config_file: Path, args):
    print_header("Jenkins 自动构建脚本")
    print(f"构建模式: {args.mode}")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 加载配置
    try:
        config = Config.load(str(config_file))
    except FileNotFoundError as e:
        log_error(str(e))
        sys.exit(1)
    
    # 解析 Job 列表
    jobs_filter = args.jobs.split(",") if args.jobs else None
    jobs = config.get_jobs(env=args.env, jobs=jobs_filter)
    
    if not jobs:
        log_error("没有找到匹配的项目")
        sys.exit(1)
    
    # 显示将要构建的项目
    print_sep("-")
    print("将要构建的 Job:")
    print_sep("-")
    for job in jobs:
        print(f"  - {job.key}: {job.path}")
    print_sep("-")
    print()
    
    # 创建日志目录
    log_dir = Path(config.build.log_dir) / f"build_{datetime.now().strftime('%Y%m%d')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"日志目录: {log_dir}")
    
    # 覆盖参数
    if args.params:
        override_params = Config._parse_params(args.params)
        for job in jobs:
            job.params.update(override_params)
    
    # 创建客户端和构建器
    client = JenkinsClient(
        url=config.server.url,
        token=config.server.token,
        timeout=config.build.curl_timeout
    )
    builder = Builder(client, config)
    
    # 执行构建
    if args.mode == "parallel":
        results = builder.build_parallel(jobs, str(log_dir))
    else:
        results = builder.build_sequential(jobs, str(log_dir))
    
    # 保存历史
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    for result in results:
        job = next((j for j in jobs if j.key == result.job_key), None)
        manager.add(BuildRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            env=job.env if job else "",
            job_key=result.job_key,
            build_num=result.build_num,
            status=result.status.value,
            duration=result.duration,
            log_file=result.log_file
        ))
    
    # 生成报告
    generate_report(results, str(log_dir))


def generate_report(results: list[BuildResult], log_dir: str):
    print_header("构建结果汇总")
    
    total = len(results)
    success = sum(1 for r in results if r.status == BuildStatus.SUCCESS)
    failure = total - success
    
    print(f"总计: {total} 个 Job")
    print(f"成功: {success} 个")
    print(f"失败: {failure} 个")
    print()
    
    print_sep("-")
    print("详细结果:")
    print_sep("-")
    
    for result in results:
        if result.status == BuildStatus.SUCCESS:
            print(f"  ✓ {result.job_key}: SUCCESS (#{result.build_num})")
        elif result.status == BuildStatus.FAILURE:
            print(f"  ✗ {result.job_key}: FAILURE (#{result.build_num})")
        elif result.status == BuildStatus.ABORTED:
            print(f"  ! {result.job_key}: ABORTED (#{result.build_num})")
        elif result.status == BuildStatus.TIMEOUT:
            print(f"  ✗ {result.job_key}: TIMEOUT (#{result.build_num})")
        else:
            print(f"  ? {result.job_key}: {result.status.value} (#{result.build_num})")
    
    print_sep("-")
    print(f"日志目录: {log_dir}")
    print_sep("=")
    
    if failure > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 2: 运行完整测试**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/ -v`
Expected: All PASS

**Step 3: Commit**

```bash
git add jenkins_config/cli.py
git commit -m "feat: add CLI entry point with full command support"
```

---

## Task 8: 更新 Shell 入口

**Files:**
- Modify: `jenkins-auto-build.sh`

**Step 1: 更新 Shell 脚本**

```bash
#!/bin/bash
# jenkins-auto-build.sh - Python CLI 包装器
# ============================================================================
# Jenkins 自动构建脚本入口
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 使用 uv 运行 Python 模块
exec uv run python -m jenkins_config.cli "$@"
```

**Step 2: 验证命令**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && ./jenkins-auto-build.sh --help`
Expected: 显示帮助信息

**Step 3: Commit**

```bash
git add jenkins-auto-build.sh
git commit -m "refactor: simplify shell script to Python CLI wrapper"
```

---

## Task 9: 清理旧文件

**Files:**
- Delete: `load-config.py`

**Step 1: 删除已废弃的文件**

```bash
rm load-config.py
```

**Step 2: Commit**

```bash
git add -A
git commit -m "chore: remove deprecated load-config.py (merged into config.py)"
```

---

## Task 10: 最终验证

**Step 1: 运行所有测试**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && uv run pytest tests/ -v`
Expected: All PASS

**Step 2: 验证列表命令**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && ./jenkins-auto-build.sh --list-envs`
Expected: 显示所有环境

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && ./jenkins-auto-build.sh --list-projects dev`
Expected: 显示 dev 环境项目

**Step 3: 验证历史命令**

Run: `cd C:/Users/ajiu/Desktop/jenkins-config && ./jenkins-auto-build.sh --history-stats`
Expected: 显示统计信息

**Step 4: 最终 Commit**

```bash
git add -A
git commit -m "docs: complete Python refactor with tests and documentation"
```

---

## 完成检查清单

- [ ] 所有测试通过
- [ ] `--list-envs` 命令正常
- [ ] `--list-projects` 命令正常
- [ ] `--history` 命令正常
- [ ] `--history-stats` 命令正常
- [ ] Shell 入口正常工作
- [ ] 移除了 `jq`、`node` 依赖
- [ ] 配置文件格式兼容