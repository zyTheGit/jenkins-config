# Jenkins 自动构建工具

将 Shell 脚本重构为模块化 Python 项目，移除外部依赖（curl、jq、node），添加历史记录功能和交互式选择界面。

## 功能特性

- **初始化模板** - `--init` 快速生成配置文件模板，支持交互式引导
- **并行/顺序构建** - 支持同时构建多个项目或按顺序逐个构建
- **交互式选择** - 终端界面选择要构建的环境和项目
- **构建历史** - 自动记录构建结果，支持查看统计
- **独立 EXE** - 可打包成单个可执行文件，无需安装 Python

## 快速开始

### 方式一：使用 Shell/PowerShell 脚本（需要 Python）

```bash
# macOS / Linux
./jenkins-auto-build.sh --help

# Windows (PowerShell)
./jenkins-auto-build.ps1 --help
```

### 方式二：使用 EXE（无需 Python）

```bash
# 直接下载 dist/jenkins-build.exe
# 将 jenkins-config.yaml 放在 exe 同级目录

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
# 生成配置文件模板（首次使用）
./jenkins-auto-build.sh --init

# 交互式引导生成配置文件
./jenkins-auto-build.sh --init -i

# 强制覆盖已有配置
./jenkins-auto-build.sh --init --force

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

# 重建上次构建的项目
./jenkins-auto-build.sh -r

# 查看构建历史
./jenkins-auto-build.sh --history
./jenkins-auto-build.sh --history-stats
```

### `--init` 交互式引导流程

```bash
./jenkins-auto-build.sh --init -i
```

通过问答形式逐步生成配置文件，流程如下：

1. **Jenkins 服务器信息** — 输入地址、用户名、API Token
2. **构建行为配置** — 选择默认配置或自定义（轮询间隔、超时等）
3. **环境与项目配置** — 逐个添加环境（dev/test/prod），每个环境可添加多个项目，指定分支

### 交互式构建模式

```bash
./jenkins-auto-build.sh -i
```

交互流程：
1. 选择构建方式（按环境/按项目）
2. 选择要构建的项目（支持多选）
3. 选择构建模式（并行/顺序）
4. 确认后开始构建

### 自定义分支与参数

```bash
# 覆盖配置文件中的分支（所有项目统一使用）
./jenkins-auto-build.sh -e dev -b feature/new-ui

# 额外传递构建参数
./jenkins-auto-build.sh -e dev -p "skip_tests=true&notify=false"
```

## 打包成 EXE

### 安装打包工具

```bash
uv pip install pyinstaller
```

### 打包命令

```bash
# 单文件模式（默认，便于分发，约 15 MB）
uv run python build.py

# 目录模式（启动更快，文件较多）
uv run python build.py --dir

# 自定义 exe 图标
uv run python build.py --icon assets/my-icon.ico

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

1. 将 `jenkins-config.yaml` 放在 exe 同级目录
2. 或使用 `-c` 参数指定配置文件路径

```bash
# 配置文件在同级目录
jenkins-build.exe --list-envs

# 指定配置文件路径
jenkins-build.exe -c /path/to/config.yaml --list-envs
```

## 项目结构

```
jenkins-config/
├── jenkins-auto-build.sh       # Shell 入口（Python 包装器）
├── jenkins-auto-build.ps1      # PowerShell 入口
├── pyproject.toml              # Python 项目配置
├── jenkins-config.yaml         # 配置文件（默认）
├── jenkins-config.example.yaml # 配置示例（YAML，推荐）
├── jenkins-config.example.json # 配置示例（JSON，兼容）
├── build.py                    # PyInstaller 打包脚本
├── entry_point.py              # EXE 入口点
├── jenkins_config/             # Python 包
│   ├── __init__.py
│   ├── cli.py                  # CLI 入口
│   ├── cmd_build.py            # 构建执行
│   ├── cmd_init.py             # 配置初始化
│   ├── cmd_interactive.py      # 交互式选择
│   ├── cmd_list.py             # 列表查询
│   ├── config_types.py         # 配置数据类型（纯 dataclass）
│   ├── config_io.py            # 配置 I/O（YAML/JSON 加载、保存）
│   ├── config.py               # 配置业务方法（猴子补丁）
│   ├── builder.py              # 构建编排（并行/顺序）
│   ├── jenkins.py              # Jenkins API 客户端
│   ├── history.py              # 构建历史
│   ├── build_result.py         # 构建结果数据类
│   ├── build_errors.py         # 错误日志生成
│   └── utils.py                # 工具函数
├── tests/                      # 测试套件
├── data/                       # 数据目录
│   └── build_history.json      # 构建历史
└── dist/                       # 打包输出
    └── jenkins-build.exe
```

## 配置文件格式

默认使用 YAML 格式（支持注释），JSON 格式仍兼容。

### 完整示例

```yaml
server:
  url: "http://jenkins.example.com:8080"
  username: admin
  token: "your-api-token"

# branch_field: CLI -b 使用的参数名，默认 "branch"
branch_field: BRANCH

build:
  mode: parallel
  poll_interval: 10
  build_timeout: 3600
  curl_timeout: 30
  log_dir: ./jenkins_logs
  log_retention_days: 3

environments:
  dev:
    description: 开发环境
    params:
      BRANCH: develop
    projects:
      - name: project-a
        path: folder/project-a     # 可选，默认等于 name
        params:
          BRANCH: feature
          SKIP_TESTS: "true"
      - name: project-b

  test:
    description: 测试环境
    params:
      BRANCH: test
    projects:
      - name: project-a
      - name: project-b

  prod:
    description: 生产环境
    branch_field: BRANCH           # 环境级覆盖 branch_field
    projects:
      - name: project-a-prod
        params:
          BRANCH: prod
      - name: project-b-prod
        params:
          BRANCH: main
```

### 配置说明

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `server.url` | Jenkins 服务器地址 | 必填 |
| `server.username` | Jenkins 登录用户名 | admin |
| `server.token` | API Token | 必填 |
| `branch_field` | CLI `-b` 使用的参数名 | branch |
| `build.mode` | 构建模式：parallel/sequential | parallel |
| `build.poll_interval` | 轮询间隔（秒） | 10 |
| `build.build_timeout` | 构建超时（秒） | 3600 |
| `build.curl_timeout` | HTTP 请求超时（秒） | 30 |
| `build.log_dir` | 日志目录 | ./jenkins_logs |
| `build.log_retention_days` | 日志保留天数（超过自动清理） | 3 |
| `environments.*.branch_field` | 环境级覆盖 branch_field | 继承全局 |
| `environments.*.params` | 环境参数（dict） | - |
| `environments.*.projects[].params` | 项目参数（dict，覆盖环境参数） | - |

### branch_field 与参数体系

所有 Jenkins 构建参数通过 `params: dict` 传递，无需硬编码字段：

- **`Config.branch_field`** — 全局默认值 `"branch"`，告诉 CLI `-b` 应覆盖哪个参数 key
- **`Environment.branch_field`** — 环境级覆盖，优先级高于全局
- **`Job.branch`** — 由 `get_jobs()` 从 `params[branch_field]` 派生，不是独立字段

```yaml
# 全局 branch_field 为 BRANCH
branch_field: BRANCH

environments:
  dev:
    params:
      BRANCH: develop        # Job.branch = "develop"
    projects:
      - name: project-a
        params:
          BRANCH: feature    # 项目级覆盖，Job.branch = "feature"
```

**参数合并优先级：** CLI `-p` > 项目 `params` > 环境 `params`（简单 `dict.update()` 链）

**向后兼容：** 旧 JSON 配置中的 `branch`、`git_param`、`default_branch` 字段仍可加载，会自动转换并输出弃用警告。`params` 同时支持 dict 格式（`{BRANCH: develop}`）和旧字符串格式（`"BRANCH=develop&skip_tests=false"`）。

## CLI 命令参考

| 命令 | 说明 |
|------|------|
| `--help` | 显示帮助信息 |
| `--help-config` | 显示配置文件模板（含字段说明） |
| `--init` | 生成配置文件模板（结合 `-i` 交互式引导） |
| `--force` | 强制覆盖已存在的配置文件（结合 `--init` 使用） |
| `-e, --env ENV` | 构建指定环境 |
| `-j, --jobs JOBS` | 构建指定项目（格式: env:project） |
| `-b, --branch BRANCH` | 自定义构建分支，覆盖配置中的分支 |
| `-m, --mode MODE` | 构建模式：parallel/sequential |
| `-p, --params PARAMS` | 额外构建参数（格式: key=val&key2=val2） |
| `-c, --config FILE` | 配置文件路径 |
| `-i, --interactive` | 交互式选择模式 |
| `-y, --yes` | 跳过确认直接构建 |
| `-r, --rebuild-last` | 重建上次构建的项目 |
| `-d, --debug` | 启用调试模式 |
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

# 带覆盖率
uv run pytest tests/ --cov=jenkins_config --cov-report=term-missing -v
```

## 架构说明

```
┌─────────────────────────────────────────────────────────────┐
│                         cli.py                               │
│                    (命令行入口，懒加载 cmd_*)                 │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ config_types  │   │  builder.py   │   │  history.py   │
│ config_io     │   │  (构建编排)   │   │  (历史记录)   │
│ config.py     │   └───────────────┘   └───────────────┘
│ (配置加载)    │           │
└───────────────┘           ▼
                    ┌───────────────┐
                    │  jenkins.py   │
                    │ (Jenkins API) │
                    └───────────────┘
```

## 故障排除

### 配置文件不存在

```
错误：配置文件不存在: jenkins-config.yaml
```

解决：使用 `--init` 快速生成配置文件模板，或使用 `-c` 参数指定已有配置路径。

```bash
# 生成配置文件模板（含 dev/test/prod 示例环境）
./jenkins-auto-build.sh --init

# 交互式引导填写服务器信息
./jenkins-auto-build.sh --init -i
```

### EXE 无法找到配置文件

EXE 模式下，配置文件查找顺序：
1. 当前工作目录
2. EXE 所在目录

### Jenkins 触发构建返回 400

Jenkins 返回 400（Bad Request）通常是因为参数值不合法：

- **Pipeline 项目**使用 `git-parameter-plugin` 时，插件会验证分支值是否在仓库中存在
- 分支值不要带 `origin/` 前缀，使用 `prod` 而不是 `origin/prod`
- FreeStyle 项目不做验证，但建议统一不带 `origin/` 前缀

### Jenkins 连接失败

检查：
- Jenkins 服务器地址是否正确
- API Token 是否有效
- 网络是否连通

## License

MIT
