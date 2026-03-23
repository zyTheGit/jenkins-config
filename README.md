# Jenkins 自动构建工具

将 Shell 脚本重构为模块化 Python 项目，移除外部依赖（curl、jq、node），添加历史记录功能和交互式选择界面。

## 功能特性

- **并行/顺序构建** - 支持同时构建多个项目或按顺序逐个构建
- **交互式选择** - 终端界面选择要构建的环境和项目
- **构建历史** - 自动记录构建结果，支持查看统计
- **独立 EXE** - 可打包成单个可执行文件，无需安装 Python

## 快速开始

### 方式一：使用 Shell 脚本（需要 Python）

```bash
# 克隆项目
git clone <repo-url>
cd jenkins-config

# 安装依赖
uv sync

# 运行
./jenkins-auto-build.sh --help
```

### 方式二：使用 EXE（无需 Python）

```bash
# 直接下载 dist/jenkins-build.exe
# 将 jenkins-config.json 放在 exe 同级目录

jenkins-build.exe --help
```

## 安装

### 前置要求

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装依赖

```bash
uv sync
```

## 使用方法

### 基本命令

```bash
# 显示帮助
./jenkins-auto-build.sh --help

# 列出所有环境
./jenkins-auto-build.sh --list-envs

# 列出项目
./jenkins-auto-build.sh --list-projects dev

# 交互式选择（推荐）
./jenkins-auto-build.sh -i

# 构建指定环境
./jenkins-auto-build.sh -e dev

# 构建指定项目
./jenkins-auto-build.sh -j dev:project-a,test:project-b

# 查看构建历史
./jenkins-auto-build.sh --history
./jenkins-auto-build.sh --history-stats
```

### 交互式模式

```bash
./jenkins-auto-build.sh -i
```

交互流程：
1. 选择构建方式（按环境/按项目）
2. 选择要构建的项目（支持多选）
3. 选择构建模式（并行/顺序）
4. 确认后开始构建

## 打包成 EXE

### 安装打包工具

```bash
uv pip install pyinstaller
```

### 打包命令

```bash
# 单文件模式（默认，便于分发，约 14 MB）
uv run python build.py

# 目录模式（启动更快，文件较多）
uv run python build.py --dir

# 清理后重新打包
uv run python build.py --clean
```

### 打包输出

```
dist/
└── jenkins-build.exe  # 单文件模式
# 或
dist/
└── jenkins-build/     # 目录模式
    └── jenkins-build.exe
```

### EXE 使用说明

1. 将 `jenkins-config.json` 放在 exe 同级目录
2. 或使用 `-c` 参数指定配置文件路径

```bash
# 配置文件在同级目录
jenkins-build.exe --list-envs

# 指定配置文件路径
jenkins-build.exe -c /path/to/config.json --list-envs
```

## 项目结构

```
jenkins-config/
├── jenkins-auto-build.sh       # Shell 入口（Python 包装器）
├── pyproject.toml              # Python 项目配置
├── jenkins-config.json         # 配置文件
├── jenkins-config.example.json # 配置示例
├── build.py                    # PyInstaller 打包脚本
├── entry_point.py              # EXE 入口点
├── jenkins_config/             # Python 包
│   ├── __init__.py
│   ├── cli.py                  # CLI 入口
│   ├── config.py               # 配置加载
│   ├── jenkins.py              # Jenkins API 客户端
│   ├── builder.py              # 构建编排
│   ├── history.py              # 历史记录
│   └── utils.py                # 工具函数
├── tests/                      # 测试套件
│   ├── test_config.py
│   ├── test_jenkins.py
│   ├── test_builder.py
│   ├── test_history.py
│   └── test_utils.py
├── data/                       # 数据目录
│   └── build_history.json      # 构建历史
└── dist/                       # 打包输出
    └── jenkins-build.exe
```

## 配置文件格式

### 完整示例

```json
{
  "server": {
    "url": "http://jenkins.example.com:8080",
    "token": "your-api-token"
  },
  "build": {
    "mode": "parallel",
    "poll_interval": 10,
    "build_timeout": 3600,
    "curl_timeout": 30,
    "log_dir": "./jenkins_logs"
  },
  "environments": {
    "dev": {
      "default_branch": "develop",
      "params": "skip_tests=false",
      "projects": [
        {
          "name": "project-a",
          "path": "folder/project-a",
          "branch": "feature",
          "params": "debug=true"
        },
        {
          "name": "project-b"
        }
      ]
    },
    "test": {
      "default_branch": "main",
      "projects": [
        { "name": "project-a" },
        { "name": "project-b" }
      ]
    }
  }
}
```

### 配置说明

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `server.url` | Jenkins 服务器地址 | 必填 |
| `server.token` | API Token | 必填 |
| `build.mode` | 构建模式：parallel/sequential | parallel |
| `build.poll_interval` | 轮询间隔（秒） | 10 |
| `build.build_timeout` | 构建超时（秒） | 3600 |
| `build.log_dir` | 日志目录 | ./jenkins_logs |
| `environments.*.default_branch` | 默认分支 | main |
| `environments.*.params` | 环境参数 | - |

### 参数合并优先级

1. 命令行参数（`--params`）
2. 项目参数（`projects[].params`）
3. 环境参数（`environments.xxx.params`）
4. 默认值

## CLI 命令参考

| 命令 | 说明 |
|------|------|
| `--help` | 显示帮助信息 |
| `-e, --env ENV` | 构建指定环境 |
| `-j, --jobs JOBS` | 构建指定项目（格式: env:project） |
| `-m, --mode MODE` | 构建模式：parallel/sequential |
| `-p, --params PARAMS` | 额外构建参数 |
| `-c, --config FILE` | 配置文件路径 |
| `-i, --interactive` | 交互式选择模式 |
| `--list-envs` | 列出所有环境 |
| `--list-projects [ENV]` | 列出项目 |
| `--history` | 查看构建历史 |
| `--history-stats` | 查看历史统计 |

## 测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 运行单个测试文件
uv run pytest tests/test_config.py -v
```

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                         cli.py                               │
│                    (命令行入口)                               │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   config.py   │   │  builder.py   │   │  history.py   │
│  (配置加载)   │   │  (构建编排)   │   │  (历史记录)   │
└───────────────┘   └───────────────┘   └───────────────┘
        │                     │
        │                     ▼
        │           ┌───────────────┐
        │           │  jenkins.py   │
        │           │ (Jenkins API) │
        │           └───────────────┘
        │
        ▼
┌───────────────┐
│   utils.py    │
│  (工具函数)   │
└───────────────┘
```

## 故障排除

### 配置文件不存在

```
错误：配置文件不存在: jenkins-config.json
```

解决：将配置文件放在当前目录或使用 `-c` 参数指定路径。

### EXE 无法找到配置文件

EXE 模式下，配置文件查找顺序：
1. 当前工作目录
2. EXE 所在目录

### Jenkins 连接失败

检查：
- Jenkins 服务器地址是否正确
- API Token 是否有效
- 网络是否连通

## License

MIT