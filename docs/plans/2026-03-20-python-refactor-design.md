# Jenkins Auto Build - Python 重构设计

## 概述

将现有 645 行 shell 脚本重构为模块化 Python 项目，移除对 `curl`、`jq`、`node` 的依赖，添加构建历史记录功能。

## 目标

- **简化依赖**：仅需 Python + requests
- **提高可维护性**：模块化架构，单一职责
- **保留兼容性**：Shell 入口保持现有使用习惯
- **新增功能**：构建历史记录

## 项目结构

```
jenkins-config/
├── pyproject.toml              # 项目配置
├── jenkins_config/             # Python 包
│   ├── __init__.py
│   ├── cli.py                  # CLI 入口 + 参数解析
│   ├── config.py               # 配置加载
│   ├── jenkins.py              # Jenkins API 客户端
│   ├── builder.py              # 构建编排（顺序/并行）
│   ├── history.py              # 历史记录管理
│   └── utils.py                # 日志/工具函数
├── jenkins-auto-build.sh       # Shell 入口（简化版）
├── jenkins-config.json         # 配置文件
└── data/
    └── build_history.json      # 历史记录存储
```

## 模块设计

### 1. config.py - 配置模块

**职责**：加载和解析 JSON 配置文件

**数据结构**：
```python
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
    path: str
    branch: str
    params: dict

@dataclass
class Environment:
    name: str
    default_branch: str
    params: dict
    projects: list[Project]

@dataclass
class Job:
    key: str           # env_project_name
    path: str          # Jenkins job path
    branch: str
    params: dict       # 合并后的参数
    env: str

class Config:
    server: ServerConfig
    build: BuildConfig
    environments: dict[str, Environment]
    
    def get_jobs(self, env: str = None, jobs: list[str] = None) -> list[Job]:
        """获取要构建的 Job 列表"""
```

**参数优先级**（高到低）：
1. 命令行 `--params`
2. 项目配置 `projects[].params`
3. 环境配置 `environments.xxx.params`
4. 默认值（date, branch）

### 2. jenkins.py - Jenkins API 客户端

**职责**：封装 Jenkins HTTP API 调用

```python
from enum import Enum
from dataclasses import dataclass

class BuildStatus(Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    ABORTED = "ABORTED"
    BUILDING = "BUILDING"
    TIMEOUT = "TIMEOUT"

@dataclass
class BuildInfo:
    number: int
    status: BuildStatus
    result: str | None
    duration: int  # 毫秒

class JenkinsClient:
    def __init__(self, url: str, token: str, timeout: int = 30):
        self.session = requests.Session()
        self.session.auth = ("admin", token)
        self.base_url = url.rstrip("/")
        self.timeout = timeout
        
    def _get_crumb(self) -> tuple[str, str] | None:
        """获取 CSRF Token，返回 (field, value)"""
        
    def trigger_build(self, job_path: str, params: dict) -> str | None:
        """触发构建，返回 queue_url"""
        
    def get_build_number(self, queue_url: str, timeout: int = 30) -> int | None:
        """从队列获取构建编号，轮询最多 timeout 次"""
        
    def get_build_status(self, job_path: str, build_num: int) -> BuildInfo:
        """获取构建状态"""
        
    def get_build_log(self, job_path: str, build_num: int) -> str:
        """获取构建日志文本"""
```

**关键点**：
- 使用 `requests.Session` 复用连接
- URL 编码使用 `urllib.parse.quote`
- 错误处理统一返回 None 或抛异常

### 3. builder.py - 构建编排

**职责**：管理构建流程（顺序/并行）

```python
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        """顺序构建，逐个执行"""
        
    def build_parallel(self, jobs: list[Job], log_dir: str) -> list[BuildResult]:
        """并行构建，使用线程池"""
        
    def _build_single(self, job: Job, log_dir: str) -> BuildResult:
        """单个构建流程"""
        # 1. trigger_build
        # 2. get_build_number
        # 3. wait_for_build (轮询)
        # 4. get_build_log
```

**并行策略**：
- 使用 `ThreadPoolExecutor(max_workers=len(jobs))`
- 所有任务同时启动
- 收集所有结果后统一报告

### 4. history.py - 历史记录

**职责**：持久化构建记录

```python
@dataclass
class BuildRecord:
    timestamp: str      # ISO 格式
    env: str
    job_key: str
    build_num: int
    status: str
    duration: int       # 秒
    log_file: str

class HistoryManager:
    MAX_RECORDS = 100
    
    def __init__(self, history_file: str = "data/build_history.json"):
        self.history_file = Path(history_file)
        self._ensure_file()
        
    def add(self, result: BuildResult, env: str):
        """添加构建记录"""
        
    def list(self, env: str = None, limit: int = 20) -> list[BuildRecord]:
        """查询历史记录"""
        
    def stats(self) -> dict:
        """统计信息：总数、成功数、失败数、成功率"""
        
    def clear(self):
        """清空历史"""
```

**存储格式**：
```json
{
  "records": [
    {
      "timestamp": "2026-03-20T10:30:00",
      "env": "dev",
      "job_key": "dev_pms_biz_plan_web",
      "build_num": 123,
      "status": "SUCCESS",
      "duration": 120,
      "log_file": "./jenkins_logs/build_20260320/dev_pms_biz_plan_web_#123.log"
    }
  ]
}
```

### 5. utils.py - 工具函数

**职责**：日志输出、时间格式化

```python
# 日志函数（保持现有风格）
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
    """格式化时长：120 -> "2分0秒" """
    mins, secs = divmod(seconds, 60)
    return f"{mins}分{secs}秒"
```

### 6. cli.py - 命令行入口

**职责**：参数解析、流程编排

```python
def main():
    parser = argparse.ArgumentParser(description="Jenkins 自动构建工具")
    
    # 构建参数
    parser.add_argument("-m", "--mode", choices=["parallel", "sequential"], 
                        default="parallel", help="构建模式")
    parser.add_argument("-e", "--env", help="构建指定环境")
    parser.add_argument("-j", "--jobs", help="构建指定项目 (格式: env:project,env:project)")
    parser.add_argument("-p", "--params", help="构建参数")
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
```

**命令流程**：
1. `--list-envs` / `--list-projects` → 直接输出后退出
2. `--history` / `--history-stats` → 查询历史后退出
3. 构建流程：加载配置 → 匹配 Job → 执行构建 → 生成报告 → 保存历史

### 7. Shell 入口

```bash
#!/bin/bash
# jenkins-auto-build.sh - Python CLI 包装器
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec uv run python -m jenkins_config.cli "$@"
```

**注意**：使用 `exec` 替换当前进程，保持退出码。

## CLI 命令对照

| 原 Shell 命令 | Python CLI 命令 |
|-------------|----------------|
| `./jenkins-auto-build.sh --env dev` | `jenkins-build --env dev` |
| `./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web` | `jenkins-build --jobs dev:pms-biz-plan-web` |
| `./jenkins-auto-build.sh --list-envs` | `jenkins-build --list-envs` |
| `./jenkins-auto-build.sh --list-projects dev` | `jenkins-build --list-projects dev` |
| (无) | `jenkins-build --history` |
| (无) | `jenkins-build --history-stats` |

## 依赖

```toml
[project]
dependencies = [
    "requests>=2.28.0",
]

[project.scripts]
jenkins-build = "jenkins_config.cli:main"
```

## 向后兼容

1. **Shell 脚本保留**：`jenkins-auto-build.sh` 作为兼容入口
2. **参数格式不变**：所有 CLI 参数保持原格式
3. **输出风格不变**：彩色日志、分隔线、报告格式保持一致
4. **配置文件格式不变**：`jenkins-config.json` 结构保持不变

## 迁移步骤

1. 创建 Python 包结构
2. 实现 config.py、utils.py
3. 实现 jenkins.py
4. 实现 builder.py
5. 实现 history.py
6. 实现 cli.py
7. 更新 jenkins-auto-build.sh
8. 删除 load-config.py（功能已合并）
9. 测试验证

## 风险

| 风险 | 缓解措施 |
|------|---------|
| 并行构建线程安全 | 使用 ThreadPoolExecutor，无共享状态 |
| 历史文件损坏 | 读写时捕获异常，损坏时自动重建 |
| 网络超时 | 所有 HTTP 请求设置 timeout |