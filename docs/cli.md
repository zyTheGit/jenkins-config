# CLI 使用指南

## 安装

前置要求：Python 3.10+ 与 [uv](https://docs.astral.sh/uv/) 包管理器。

```bash
uv sync
```

## 两种运行方式

### 方式一：Shell / PowerShell 脚本（需要 Python）

```bash
# macOS / Linux
./jenkins-auto-build.sh --help

# Windows (PowerShell)
./jenkins-auto-build.ps1 --help
```

### 方式二：独立 EXE（无需 Python）

从 [GitHub Release](https://github.com/zyTheGit/jenkins-config/releases) 下载对应平台的 `jenkins-build` 可执行文件，把 `jenkins-config.yaml` 放在同级目录：

```bash
jenkins-build.exe --help
```

## 基本命令

```bash
# 生成配置文件模板（首次使用）
./jenkins-auto-build.sh --init

# 交互式引导生成配置文件
./jenkins-auto-build.sh --init -i

# 强制覆盖已有配置
./jenkins-auto-build.sh --init --force

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

## `--init` 交互式引导流程

```bash
./jenkins-auto-build.sh --init -i
```

通过问答形式逐步生成配置文件，流程如下：

1. **Jenkins 服务器信息** — 输入地址、用户名、API Token
2. **构建行为配置** — 选择默认配置或自定义（轮询间隔、超时等）
3. **环境与项目配置** — 逐个添加环境（dev/test/prod），每个环境可添加多个项目，指定分支

## 交互式构建模式

```bash
./jenkins-auto-build.sh -i
```

交互流程：

1. 选择构建方式（按环境/按项目）
2. 选择要构建的项目（支持多选）
3. 选择构建模式（并行/顺序）
4. 确认后开始构建

## 自定义分支与参数

```bash
# 覆盖配置文件中的分支（所有项目统一使用）
./jenkins-auto-build.sh -e dev -b feature/new-ui

# 额外传递构建参数
./jenkins-auto-build.sh -e dev -p "skip_tests=true&notify=false"
```

## 命令参考

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

