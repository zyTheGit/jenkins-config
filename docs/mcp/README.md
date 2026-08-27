# Jenkins MCP Server

## 1. 概述

Jenkins MCP Server 将 Jenkins 自动构建 CLI 工具的能力暴露为 [Model Context Protocol (MCP)](https://modelcontextprotocol.io) 协议工具，使 AI Agent（如 Claude Desktop、Cursor、VS Code Copilot 等）能够通过标准 MCP 协议调用构建功能。

**核心能力：**

- 查询环境、项目、配置等只读信息
- 触发 Jenkins 构建并快速返回（不等待构建完成，避免超时）
- 查询构建状态和日志
- 查看构建历史与统计
- 重建上次构建的项目
- 诊断构建失败原因
- 诊断配置来源（`where_config`）与本地环境体检（`doctor`，默认不发网络请求）
- 零配置起步：生成配置模板（`init_config`）

**能力清单：** 14 个 Tools（10 个只读查询类 + 4 个写入类）、4 个 Resources、3 个 Prompts。

**默认只读：** 三个操作类工具（`trigger_build`、`rebuild_last`、`save_config`）需显式设置环境变量 `JENKINS_MCP_ALLOW_WRITE=1` 才会执行，详见 §7.3。第四个写入类工具 `init_config` 采用**分级门控**：只在覆盖已有配置（`overwrite=true`）时才要求该变量，详见 §4.2。

**失败可行动：** 所有工具的失败返回都带统一载荷（`error_code` / `error` / `config_path` / `next_steps` / `docs`），错误码枚举与各码的下一步动作见 §7.5。

---

## 2. 前提条件

- **Python 3.10+** 和 [uv](https://docs.astral.sh/uv/) 包管理器（仅源码 / 控制台入口方式需要；走 §3.2 的 npx 方式时不需要）
- **mcp 可选依赖**：`mcp[cli]>=1.25.0,<2.0.0`（pyproject.toml 中的 `mcp` extra，安装方式见 §3.5）
- **Node.js 18+**（仅 §3.2 的 npx 方式需要，此时无需 Python）
- **配置文件**：探测链上任一目录中存在 `jenkins-config.yaml` / `jenkins-config.yml` / `jenkins-config.json`——每个目录都先看其 `.jenkins-config/` 子目录再看目录本身，源码模式为 项目根/.jenkins-config → 项目根 → CWD/.jenkins-config → CWD → 用户级配置目录，EXE 模式把前两组换成 CWD 与 exe 目录；也可用 `JENKINS_MCP_CONFIG` 给绝对路径（放置建议见 §3.7，完整规则见 §7.1）。没有配置文件时可直接调 `init_config` 生成模板（见 §3.9）
- **Jenkins 服务器可达**：MCP Server 需要能够访问配置中的 Jenkins 实例

---

## 3. 安装与配置

### 3.1 先选运行方式

| 方式 | 运行时依赖 | 适用场景 | 详见 |
|------|-----------|---------|------|
| **npx 启动器** | Node.js 18+ | 使用方接入，不想装 Python | §3.2 |
| **控制台入口 / 源码** | Python 3.10+ 与 uv | 本项目的开发调试 | §3.5 |
| **直接指定二进制** | 无 | 内网离线分发、CI | §3.2 的 `JENKINS_MCP_BINARY` |

无论哪种方式，**接入客户端的动作都是同一件事**：在客户端的 MCP 配置里登记一个 stdio server，给出 `command` / `args`，需要时再给 `env`。各客户端的配置位置与登记方式见 §3.4。

装完务必按 §3.8 验证一遍——MCP 客户端对"配置写错位置"通常不报错，只是列表里没有这个 server，看起来和"装了但不工作"一模一样。

### 3.2 通过 npx 一键运行（推荐给使用方，无需 Python）

面向"只想在 MCP 客户端里填一行配置"的使用方。`npm/` 目录下的启动器包 `@zythegit/jenkins-config-mcp`
首次运行时会从 GitHub Release 下载当前平台的预编译二进制（PyInstaller 打包，**自带 Python 运行时**），
校验 sha256 后缓存复用，因此目标机器只需 Node.js 18+：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"]
    }
  }
}
```

支持平台：Windows x64、macOS x64 / arm64、Linux x64 / arm64。

启动器按以下优先级解析运行方式，并以 stdio 直通拉起子进程（参数 / 环境变量 / 退出码 / 信号均透传）：

1. `JENKINS_MCP_BINARY` — 直接指定二进制路径
2. `JENKINS_MCP_PYTHON` — 指定解释器，执行 `-m jenkins_config.mcp.server`（开发用）
3. 缓存中已下载的二进制（`~/.cache/jenkins-config-mcp/<tag>/`，Windows 为 `%LOCALAPPDATA%`）
4. 从 Release 下载二进制并校验 sha256（清单为同一 Release 下的 `checksums.txt`）
5. 兜底：PATH 上的 `jenkins-config-mcp` / `uvx` / `python`

启动器自身的日志一律写 stderr，stdout 只承载 MCP 的 JSON-RPC 报文。排查启动问题时可先看解析结果：

```bash
JENKINS_MCP_LAUNCHER_DRYRUN=1 npx -y @zythegit/jenkins-config-mcp
```

可用环境变量：

- `JENKINS_MCP_VERSION` — 要下载的 Release tag，默认 `v<npm 包版本>`
- `JENKINS_MCP_RELEASE_BASE` — 下载地址前缀，默认 GitHub Release，可指向内网镜像
- `JENKINS_MCP_CACHE_DIR` — 缓存根目录
- `JENKINS_MCP_SKIP_CHECKSUM=1` — 跳过 sha256 校验（不建议）

> 二进制由 `.github/workflows/build.yml` 在 tag 推送时构建并发布，npm 包版本需与 Release tag 对齐（`v<version>`）。

### 3.3 环境变量必须写进 `env`（最常见的踩坑）

写开关、日志级别、配置路径这些变量，**只能写在 server 配置的 `env` 字段里**，在自己的 shell 里 `export` / `$env:` 是无效的：

```powershell
# ❌ 不生效：这个变量到不了 MCP Server 进程
$env:JENKINS_MCP_ALLOW_WRITE = "1"
```

原因是 stdio 传输下客户端只把一个**平台相关的白名单子集**（Windows 大致是 `PATH` / `APPDATA` / `LOCALAPPDATA` / `TEMP` / `USERPROFILE` / `SYSTEMROOT`）传给子进程，自定义变量一律丢弃。MCP 规范给出的唯一途径就是显式声明 `env`：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"],
      "env": { "JENKINS_MCP_ALLOW_WRITE": "1" }
    }
  }
}
```

> 部分客户端实现里自定义 `env` 是**替换**而非合并默认白名单（typescript-sdk issue #216）。若加了 `env` 之后反而起不来（`npx` 找不到），把 `PATH` 一起显式写进去。

启动器本身不改环境（`spawn(..., { stdio: 'inherit' })` 直接继承），所以变量丢失一定发生在客户端到启动器这一跳。

### 3.4 各客户端接入

配置位置各家不同，写错文件是"配了但没生效"的头号原因。

| 客户端 | 配置位置 | 备注 |
|--------|---------|------|
| Claude Code | `claude mcp add` 写入 `~/.claude.json`；project 作用域写仓库根的 `.mcp.json` | **不读** `claude_desktop_config.json` |
| Claude Desktop | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`、Windows `%APPDATA%\Claude\claude_desktop_config.json` | 与 Claude Code 互不相通 |
| Cursor | 项目内 `.cursor/mcp.json`，或全局 `~/.cursor/mcp.json` | |
| VS Code (Copilot) | 项目内 `.vscode/mcp.json` | 顶层键是 `servers`，不是 `mcpServers` |
| Codex CLI | `~/.codex/config.toml`，或受信任项目的 `.codex/config.toml` | TOML 格式，表名是 `mcp_servers`；CLI 与 IDE 扩展共用 |
| OpenCode | `~/.config/opencode/opencode.json`，或项目根的 `opencode.json` | 顶层键 `mcp`，环境变量键名是 `environment` |
| Pi | 项目根 `.mcp.json`，或全局 `~/.pi/agent/mcp.json` | 内核不带 MCP，需先装 `pi-mcp-adapter` |
| MCP Inspector | 无配置文件，界面上填 | 变量填在 Environment Variables 面板 |


#### Claude Code

用 CLI 登记最稳妥，不必手写 JSON、也不会写错层级：

```bash
claude mcp add jenkins-build -e JENKINS_MCP_ALLOW_WRITE=1 -- npx -y @zythegit/jenkins-config-mcp
```

`--` 之后的内容原样作为 `command` + `args`，不加 `--` 的话 `-y` 会被 `claude` 自己吃掉。作用域用 `-s` / `--scope` 指定：

- `local`（默认）— 写入 `~/.claude.json` 的 `projects.<当前目录>.mcpServers`，只在该目录下生效，不进版本库
- `project` — 写入仓库根的 `.mcp.json`，随代码提交、团队共享
- `user` — 写入 `~/.claude.json` 顶层 `mcpServers`，所有项目可用

配置文件在仓库里时用 `local` 或 `project`（Server 能从项目根探测到 `jenkins-config.yaml`）；要 `user` 全局可用，得同时用 `JENKINS_MCP_CONFIG` 给出配置文件的**绝对路径**，见 §3.7。

`project` 作用域也可以直接手写仓库根的 `.mcp.json`：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"],
      "env": { "JENKINS_MCP_ALLOW_WRITE": "1" }
    }
  }
}
```

验证与排查：

```bash
claude mcp list           # 会实际拉起 server 做健康检查，输出 ✓ Connected
claude mcp get jenkins-build
claude mcp remove jenkins-build
```

改完配置**必须重开 Claude Code**：MCP 配置只在启动时读取，不热加载。重开后 `/mcp` 里应出现 `jenkins-build` 及其 14 个工具。

排查顺序也建议照此：先 `claude mcp list` 确认登记成功（没登记 → 配置写错了地方），再看 `/mcp` 确认当前会话已加载（登记了但列表里没有 → 没重启）。

#### Claude Desktop

编辑 `claude_desktop_config.json`（macOS `~/Library/Application Support/Claude/`、Windows `%APPDATA%\Claude\`）后**完全退出并重启**应用——关窗口不算，Windows 要从托盘退出：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"],
      "env": { "JENKINS_MCP_ALLOW_WRITE": "1" }
    }
  }
}
```

启动失败时看客户端日志：macOS `~/Library/Logs/Claude/mcp-server-jenkins-build.log`、Windows `%APPDATA%\Claude\logs\`。Server 的日志全部走 stderr，会落在这里。

#### Cursor

项目内 `.cursor/mcp.json`（或全局 `~/.cursor/mcp.json`），格式同上。改完在 Settings → MCP 里点 Refresh。

#### VS Code (GitHub Copilot)

项目内 `.vscode/mcp.json`，注意顶层键是 `servers`：

```json
{
  "servers": {
    "jenkins-build": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"],
      "env": { "JENKINS_MCP_ALLOW_WRITE": "1" }
    }
  }
}
```

VS Code 的 Agent Host 不直接读 `.vscode/mcp.json`；需要跨工具通用时，改放工作区 `.mcp.json` 或 `~/.copilot/mcp-config.json`。

#### Codex CLI

配置写在 `~/.codex/config.toml`，用 TOML 而非 JSON，表名是 `mcp_servers`（下划线）。放到受信任项目的 `.codex/config.toml` 可做项目级限定，CLI 与 IDE 扩展共用这份配置：

```toml
[mcp_servers.jenkins-build]
command = "npx"
args = ["-y", "@zythegit/jenkins-config-mcp"]
env = { JENKINS_MCP_ALLOW_WRITE = "1" }
```

也可以用命令登记，效果相同；TUI 里用 `/mcp` 查看已连接的服务器：

```bash
codex mcp add jenkins-build --env JENKINS_MCP_ALLOW_WRITE=1 -- npx -y @zythegit/jenkins-config-mcp
```

#### OpenCode

配置在 `~/.config/opencode/opencode.json`（或项目根的 `opencode.json`）的 `mcp` 键下。环境变量的键名是 `environment`，**不是** `env`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "jenkins-build": {
      "type": "local",
      "command": ["npx", "-y", "@zythegit/jenkins-config-mcp"],
      "environment": { "JENKINS_MCP_ALLOW_WRITE": "1" },
      "enabled": true
    }
  }
}
```

#### Pi

Pi 内核刻意不内置 MCP，需要先装社区适配器并重启 Pi：

```bash
pi install npm:pi-mcp-adapter
```

适配器读取标准的 `.mcp.json`（项目根）或全局 `~/.pi/agent/mcp.json`，格式与 §3.2 那段 `mcpServers` 完全一致，直接复制即可。装好后用 `/mcp` 面板查看连接状态。

#### MCP Inspector


Inspector 没有配置文件，Command 填 `npx`、Arguments 填 `-y @zythegit/jenkins-config-mcp`。变量要在左侧 **Environment Variables** 面板里 Add（Key `JENKINS_MCP_ALLOW_WRITE`、Value `1`），填完 Disconnect 再 Connect 才生效——终端里 export 同样无效，理由见 §3.3。

调本地源码则用：

```bash
uv run mcp dev jenkins_config/mcp/server.py
```

### 3.5 通过控制台入口运行（需要 Python）

适用于已安装 `jenkins-config` 包的本地开发环境。先装 pyproject.toml 中的可选依赖 extra `mcp`：

```bash
# 源码开发环境（推荐）
uv sync --extra mcp

# 或将本项目作为包安装时
pip install "jenkins-config[mcp]"
```

未安装 mcp 依赖时，`jenkins_config.mcp.server` 模块本身仍可被导入（依赖延迟到首次使用时才加载），但启动入口 `jenkins-config-mcp` 会输出友好提示并以退出码 1 退出：`缺少 mcp 依赖，请执行: pip install jenkins-config[mcp]`。

**Claude Desktop** (`claude_desktop_config.json`)：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/jenkins-config", "jenkins-config-mcp"]
    }
  }
}
```

**Cursor** (`.cursor/mcp.json`)：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/jenkins-config", "jenkins-config-mcp"]
    }
  }
}
```

> 将 `/path/to/jenkins-config` 替换为项目实际路径。

默认为**只读模式**，`trigger_build` / `rebuild_last` / `save_config` 会被拒绝。如需允许 AI 代为触发构建，在 server 配置中注入环境变量（详见 §7.3）：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/jenkins-config", "jenkins-config-mcp"],
      "env": { "JENKINS_MCP_ALLOW_WRITE": "1" }
    }
  }
}
```

### 3.6 本地开发模式

适用于开发调试，直接运行 Python 模块（效果与 `jenkins-config-mcp` 入口一致）：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/jenkins-config",
        "python", "-m", "jenkins_config.mcp.server"
      ]
    }
  }
}
```

Claude Code 下等价的登记命令：

```bash
claude mcp add jenkins-build -- uv run --directory /path/to/jenkins-config jenkins-config-mcp
```

### 3.7 配置文件放在哪里

npx / EXE 方式没有"项目目录"这个概念，Server 由客户端拉起时 CWD 可能是 `/` 或家目录，所以**必须让它能找到 `jenkins-config.yaml`**。两种做法：

1. 放到用户级配置目录（探测链的末位，最省事）：三平台统一为 `~/.jenkins-config/jenkins-config.yaml`（Windows 即 `%USERPROFILE%\.jenkins-config\jenkins-config.yaml`）
2. 用 `JENKINS_MCP_CONFIG` 显式指路（**只接受绝对路径**，相对路径会记 warning 后按未设置处理）：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"],
      "env": {
        "JENKINS_MCP_CONFIG": "C:\\work\\jenkins-config\\jenkins-config.yaml",
        "JENKINS_MCP_ALLOW_WRITE": "1"
      }
    }
  }
}
```

该变量**只对 MCP Server 生效**，CLI 的探测不读它——否则为客户端导出一次，项目目录里的 `jenkins-build` 也会跟着换配置。完整的解析顺序与构建历史落盘位置见 §7.1 / §7.4。

配置文件用 CLI 生成：`jenkins-build --init -i`（或 `./jenkins-auto-build.sh --init -i`）。

### 3.8 验证安装

按这个顺序走，每步都能把问题圈到一层：

```bash
# 1. 包能下到、平台二进制能解析出来（只打印命令，不启动）
JENKINS_MCP_LAUNCHER_DRYRUN=1 npx -y @zythegit/jenkins-config-mcp

# 2. 客户端确实登记了这个 server（Claude Code）
claude mcp list
```

3. 重启客户端，确认工具列表里有 `jenkins-build`（Claude Code 用 `/mcp`）。
4. 调 `doctor` — 一次性看到配置有没有找到、有没有填完、写开关状态；默认不发网络请求，断网也能用。
5. 调 `list_environments` — 通了说明配置文件找到了。
6. 调 `health_check` — 这是唯一能证明 Jenkins 地址与 token 都有效的工具，返回 `reachable: true` 才算真正可用。
7. 需要写操作时再调 `trigger_build`；若返回 `error_code: write_not_allowed`，说明 `JENKINS_MCP_ALLOW_WRITE` 没送达，回到 §3.3。

任何一步失败时，返回体里的 `error_code` 与 `next_steps` 就是下一步动作，字段含义见 §7.5。

### 3.9 零配置上手流程（npx 用户最短路径）

npx 用户装完之后没有任何配置文件，从零到 `list_environments` 成功的最短路径是四步。让 AI 客户端直接套用 `setup_workflow` prompt（见 §6）即可，手动执行则是：

1. 调 `doctor` — 若 `config_located` 为 `error`，说明探测链上没有配置文件（这是全新安装的正常状态）。
2. 调 `init_config`（默认 `target="user"`）— 在 `~/.jenkins-config/jenkins-config.yaml` 生成模板。目标不存在时**不需要** `JENKINS_MCP_ALLOW_WRITE`，所以不必先改 `mcp.json` 再重启客户端。返回 `error_code: config_exists` 表示已有配置，不要覆盖，改去第 3 步直接编辑它。
3. 打开返回的 `path`，把 `server.url`、`server.token` 从占位符（`http://your-jenkins-server:8080` / `your-api-token`）改成真实取值，并按 `template_fields` 中 `required=true` 的键补齐 `environments` 下的环境与项目。token 属于凭据，只能由用户本人填写。
4. 调 `list_environments` 验证 — 能列出真实环境即生效；仍失败就按返回体的 `error_code` / `next_steps` 处理，或再调一次 `doctor` 看卡在哪一层。

需要 AI 代为触发构建时，再按 §3.3 注入 `JENKINS_MCP_ALLOW_WRITE=1` 并重启客户端。

> ⚠️ **路径会进入模型上下文**：`where_config` 与 `doctor` 的返回体包含配置文件、历史文件、日志目录的**绝对路径**，在 Windows / macOS 上通常含系统用户名（如 `C:\Users\<用户名>\.jenkins-config\...`）。这些内容会随工具返回进入模型上下文，介意时不要在公共会话里调用，或改用 `JENKINS_MCP_CONFIG` 把配置放到不含个人信息的路径下。两个工具都只报路径与状态，不回显 `server.token` 等凭据原文（`doctor` 只说"已配置 / 未配置"）。

---

## 4. 可用工具列表

共 14 个工具：10 个只读查询类 + 4 个写入类（`trigger_build`、`rebuild_last`、`save_config`、`init_config`）。
所有工具的 `config_path` 参数均可省略，省略时按 §7.1 的规则自动探测配置文件。

选哪个工具的边界：

- `show_config` 答"配置里写了什么"（内容摘要，token 脱敏）；`where_config` 答"这份配置从哪来"（路径、来源、候选目录顺序），两者字段刻意不重叠
- `health_check` 是单点网络探测（Jenkins 通不通）；`doctor` 是本地环境体检（配置 / 权限 / 历史 / 日志 / 运行模式），默认零网络请求

失败返回统一带 `error_code` / `error` / `config_path` / `next_steps` / `docs`（见 §7.5）：

- dict 型工具把五个字段合并到**顶层**（`health_check` 另保留既有的 `reachable` / `url`）
- list 型工具（`list_environments` / `list_projects` / `show_history`）返回**单元素列表**，该元素只含上述五个键，不再伪装成业务数据
- `trigger_build` / `rebuild_last` 保持 `triggered` / `failed` 容器结构，四个可行动字段追加在顶层


### 4.1 只读查询类

#### `list_environments`

列出所有配置的构建环境。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `list[dict[str, Any]]` — 环境信息列表，每项包含 `name`（环境名称）和 `description`（描述）。加载配置失败时返回**单元素列表**，该元素为统一失败载荷（只含 `error_code` / `error` / `config_path` / `next_steps` / `docs`，不含 `name` / `description`）。

---

#### `list_projects`

列出配置中的项目，可按环境过滤。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `env` | `str` | 否 | 环境名称，为空时列出所有环境的项目 |
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `list[dict[str, Any]]` — 项目列表，每项包含 `environment`（环境名）、`name`（项目名）和 `path`（Job 路径）。加载配置失败时返回单元素列表，该元素为统一失败载荷（不含 `environment` / `name` / `path`）。

---

#### `show_config`

显示当前配置摘要，**Token 完全脱敏**：不保留任何原始字符，仅显示为 `*** (长度 N)`（token 为空时为 `***`）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `dict` — 包含 `server_url`、`username`、`token`（完全脱敏）、`environments` 和 `build_config`（含 `mode`、`poll_interval`、`queue_timeout`、`build_timeout`、`curl_timeout`、`max_parallel`、`log_dir`、`log_retention_days`）。加载失败时返回统一失败载荷（顶层五字段）。

---

#### `health_check`

检查 Jenkins 服务器是否可达。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `dict` — 包含 `reachable`（是否可达）和 `url`（服务器地址）；失败时在这两个键之外合并统一失败载荷（`error_code` / `error` / `config_path` / `next_steps` / `docs`）。连接失败（`ConnectionError`）归为 `unknown_error`，`next_steps` 指向"调 `doctor` 复查配置 / 确认地址可达 / 确认 token 未过期"——网络失败不会被误判成配置权限问题。

---

#### `show_history`

查询构建历史记录，按时间倒序排列。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `env` | `str` | 否 | 按环境名称过滤，为空时返回所有环境 |
| `limit` | `int` | 否 | 返回的最大记录数量，默认 `20` |
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `list[dict[str, Any]]` — BuildRecord 字典列表。历史文件锚定在配置文件所在目录的 `data/build_history.json`。读取失败时返回单元素列表，该元素为统一失败载荷（只含五个键，不再塞 `path` 之类的伪记录字段）。

---

#### `show_history_stats`

查询构建历史统计。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `dict` — 包含 `total`（总数）、`success`（成功数）、`failure`（失败数）、`building`（未落终态的 `BUILDING` 占位记录数）、`other`（`ABORTED` / `CANCELLED` 等既不算成功也不算失败的终态数）和 `success_rate`（成功率，格式化为百分比字符串，如 `"95.0%"`）。`building` 与 `other` 都**不参与成功率分母**（分母恒为 `success + failure`）；分母为 0 时成功率为 `"0%"`。查询失败时返回统一失败载荷（顶层五字段）。


---

#### `get_build_status`

查询 Jenkins 构建状态。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `job_path` | `str` | 是 | Jenkins Job 路径，如 `"my-project"` 或 `"folder/my-project"` |
| `build_num` | `int` | 是 | 构建编号 |
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `dict` — 包含 `number`（编号）、`status`（状态）、`result`（结果）和 `duration`（耗时，可读描述如 `"3m 20s"`）。查询失败时 `status` 为 `UNKNOWN` 并附加 `error`。

---

#### `get_build_log`

获取 Jenkins 构建日志文本。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `job_path` | `str` | 是 | Jenkins Job 路径 |
| `build_num` | `int` | 是 | 构建编号 |
| `tail_kb` | `int` | 否 | 最多返回的日志尾部大小（KB），默认 `50`；传 `0` 或负数表示不限制（超大日志会全量载入内存，不推荐） |
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `str` — 构建日志文本；未获取到日志或失败时返回提示/错误信息文本。

日志超过 `tail_kb` 时以流式方式读取并**只保留尾部**，返回文本开头附加一行截断说明（含原始字节数与保留字节数），避免超大日志占满内存或撑爆模型上下文。

---

#### `where_config`

诊断配置文件的锚定结果，回答"这次到底读的是哪份配置、为什么是它"。**只做路径层面的探测，全程不加载配置内容**，因此返回体不可能出现 `server.url` / `token` 等字段。

参数：

- `config_path`（`str`，否）— 显式指定的配置文件路径，为空时按环境变量 / 自动探测

**返回值：** `dict`

- `config_path` — 本次会读取的配置文件绝对路径
- `path_allowed` — 该路径是否落在 `allowed_config_bases` 之内
- `exists` — 该文件是否存在（**仅当 `path_allowed=true` 时才返回该键**）
- `source` — 路径来源：`explicit_arg`（显式传参）/ `env_var`（`JENKINS_MCP_CONFIG` 生效）/ `probed`（候选目录命中）/ `fallback`（都没命中，退回首个候选）
- `env_var` — `{name, value, effective}`：变量名恒为 `JENKINS_MCP_CONFIG`，`effective` 说明它这次是否真正生效（传了相对路径或同时给了 `config_path` 时为 `false`）
- `mode` — 运行模式 `source` / `frozen`
- `search_bases` — 候选目录明细，每项含 `base`（被跳过时为 `null`）、`order`（从 1 递增）、`kind`（`project_root_app_dir` / `project_root` / `cwd_app_dir` / `cwd` / `exe_dir_app_dir` / `exe_dir` / `user_config_dir`，带 `_app_dir` 后缀的是该目录下的 `.jenkins-config` 子目录）、`exists`、`matched_file`（命中的配置文件，未命中为空串）、`skipped_reason`（`''` / `home_unavailable` / `too_broad`）、`allowed`（是否在白名单允许范围内）
- `candidate_file_names` — 探测用的文件名顺序
- `history_path` — 对应的构建历史文件路径（**仅当 `path_allowed=true` 时才返回该键**）
- `allowed_config_bases` — 当前允许读写配置的根目录列表（即 `config_path` 白名单）

传入的 `config_path` 越界时（`path_allowed=false`），返回体**省略 `exists` 与 `history_path`**，改带 §7.5 的失败载荷字段（`error_code=config_path_denied` + `next_steps`）。这样做是为了不让本 tool 变成"任意路径存在性探针"绕过白名单——诊断需要的是"为什么被拒、怎么放行"，而不是它在不在那儿；`search_bases` / `allowed_config_bases` 等诊断信息照常返回。

探测本身失败时返回 §7.5 的失败载荷（`error_code=unknown_error` + 非空 `next_steps`），而不是只带 `error` 的字典——没有 `error_code` 的返回体无法被机械判错，也拿不到下一步动作。


> `search_bases` / `allowed_config_bases` 里都是绝对路径，通常含系统用户名，会随返回值进入模型上下文，见 §3.9 的提示。

---

#### `doctor`

本地环境体检：一次性把配置、写开关、主机白名单、历史文件、日志落点、运行模式摊开成固定 11 项检查。**默认零网络请求**，断网或凭据未配好时同样可用；凭据只以"键名 + 已配置 / 未配置"呈现，不含 token 原文。

参数：

- `config_path`（`str`，否）— 配置文件路径，为空时自动探测
- `include_jenkins`（`bool`，否）— 默认 `false`；为 `true` 时才追加一次 Jenkins 连通性检测（会发网络请求）

**返回值：** `dict`

- `status` — 整体结论 `ok` / `warn` / `error`（取所有参与项中最严重者）
- `checks` — 11 项检查，每项含 `name` / `status`（`ok` / `warn` / `error` / `skip`）/ `detail` / `hint`（`ok` 时为空串）
- `summary` — 各状态计数 `{ok, warn, error, skip}`
- `config_path` — 本次体检针对的配置文件路径
- `next_steps` — 汇总自失败项的 `hint`（error 优先于 warn）；`status` 为 `ok` 时为空列表

11 个检查项：

- `config_located` — 配置文件是否命中（`source` 为 `fallback` 或文件不存在即 error）
- `config_readable` — 文件可读性（区分"是目录"与"没权限"）
- `config_parsable` — 能否解析并构造出 `Config`
- `config_complete` — `server.url` / `server.token` 是否仍是模板占位符（判据与模板同源，见 `config_io.PLACEHOLDER_VALUES`）
- `config_path_allowed` — 路径是否在白名单允许范围内
- `write_gate` — `JENKINS_MCP_ALLOW_WRITE` 状态
- `allowed_hosts` — `JENKINS_MCP_ALLOWED_HOSTS` 是否显式设置
- `history_path` — 历史文件可读 / 目录可写（**只读探测，绝不创建目录**；文件尚不存在但目录可写判 ok，全新安装不算故障）
- `log_sink` — 当前日志级别与实际落点（请求了文件日志但降级为仅 stderr 时判 warn）
- `runtime_mode` — 运行模式、包版本、进程 CWD，**恒为 ok 且不参与整体 status 升级**
- `jenkins_reachable` — 默认 `skip`；`include_jenkins=true` 时才真正探测

判级约定：`write_gate` 与 `allowed_hosts` 未设置**只判 warn**，因为只读模式与"退回配置文件 `server.url`"都是有意的默认值；warn 不会把整体拉成 error，避免 doctor 在正常只读部署下常态报红。配置类检查一旦某层失败，其下游一律记 `skip` 而不是重复报 error。

> `detail` 里含配置、历史、日志的绝对路径（通常含系统用户名），同样会进入模型上下文，见 §3.9。

---

### 4.2 写入类

#### `trigger_build`

触发 Jenkins 构建。触发后快速返回，不等待构建完成。
默认等待 Jenkins 分配构建编号（配置文件模式为 `min(queue_timeout, 15)` 秒，直连/内部默认为 10 秒），以覆盖 Jenkins 队列默认的 5 秒静默期；多个 Job 的编号探测**并发执行**，总耗时不随项目数量线性增长。超时仍未分配编号时不阻塞返回，对应历史记录以 `build_num=0` 落盘且不参与 `rebuild_last` 的重建分组。
触发成功后会向历史文件写入 **`BUILDING` 占位记录**（见下方警告）。

> ⚠️ **写操作**：需先设置环境变量 `JENKINS_MCP_ALLOW_WRITE=1`，否则直接返回拒绝信息且不触发任何构建（见 §7.3）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `env` | `str` | 是 | 环境名称，不能为空 |
| `projects` | `str` | 否 | 逗号分隔的项目名称，为空时触发该环境下所有项目 |
| `branch` | `str` | 否 | 自定义分支名，覆盖配置中的分支参数（参数名按各 Job 所属环境解析，环境级 `branch_field` 优先于全局） |
| `params` | `str` | 否 | 额外构建参数，支持 JSON 格式或 `key=value&key=value` 格式。以 `{` 开头即按 JSON 严格解析，**解析失败直接报错**（不再静默回退为 `key=value`）；**JSON 仅接受标量值**（字符串/数字/布尔），`null`、列表、字典等非标量值会被跳过并在返回值的 `skipped_params` 中列出；布尔值转为小写 `"true"`/`"false"`，其余统一转为字符串 |
| `wait_build_num` | `bool` | 否 | 是否等待 Jenkins 分配构建编号，默认 `true`；置为 `false` 时立即返回，`build_num` 为 `null`、仅带 `queue_url` |
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `dict` — 包含 `triggered`（已触发列表）和 `failed`（失败列表）：

- `triggered` 每项包含 `job_key`、`queue_url`、`build_num`（编号未分配时为 `null`）和 `note` 字段。`note` 显式提示：历史记录为 `BUILDING` 占位记录，实际结果请用 `get_build_status` 查询；编号未分配时还会说明 `build_num=0` 不参与重建分组
- `failed` 每项包含 `job_key` 和 `error`
- `skipped_params`（可选）：`params` 中被跳过的非标量参数名列表
- `history_error`（可选）：构建已触发但历史记录写入失败时的错误信息（不影响构建本身）
- 整体失败（未开写门控、环境名为空、配置加载失败等）时容器结构不变（`triggered` 为空、`failed` 带一条人类可读 `error`），并在**顶层追加** `error_code` / `config_path` / `next_steps` / `docs`；顶层 `error` 与 `failed[0]["error"]` 同源

> ⚠️ **BUILDING 占位记录说明**：MCP 触发的构建在历史记录中以 `BUILDING` 占位状态写入，**不会自动更新为终态**。查询真实构建状态请使用 `get_build_status` 工具；因此 `--history` / `--history-stats`（及 `show_history` / `show_history_stats`）中的显示可能不准确（占位记录可能被统计为失败并稀释成功率）。返回结果的每个触发项均带 `note` 字段提示该行为。

---

#### `rebuild_last`

重建上次构建的项目。从构建历史中获取上次成功触发的项目分组（同一时间戳的 `get_last_build_group`，自动过滤 `build_num=0` 的占位记录），重新触发构建。支持两种模式：

- **配置文件模式**：未提供 `jenkins_url`/`jenkins_token` 时使用，`config_path` 为空则自动检测；从配置文件读取 Jenkins 连接信息，按记录通过 `create_job_from_record` 恢复 Job（配置中不存在的项目会被跳过并计入 `failed`）
- **直连模式**：提供 `jenkins_url` + `jenkins_token`（优先级高于 `config_path`），无需配置文件；Job 路径从历史记录的 `job_key` 推导（去除 `{env}_` 前缀后下划线转连字符），无法推导的记录跳过并计入 `failed`

直连模式行为细节：

- `history_file` 为空时，默认锚定**项目根目录**下的 `data/build_history.json`（按源码文件位置推导，与进程 CWD 无关）
- 历史文件不存在时返回 `历史文件不存在: ...` 错误
- 历史中没有可重建记录时返回 `没有找到上次成功构建的记录`
- `jenkins_username` 为空时默认 `admin`
- `jenkins_url` 受主机白名单约束：默认只允许配置文件中的 Jenkins 主机；如需放开，用 `JENKINS_MCP_ALLOWED_HOSTS` 显式声明（见 §7.3）。这样可避免调用方通过参数把凭据发往任意地址

两种模式重建成功后，都会将新的触发记录回写历史文件（仍为 `BUILDING` 占位记录，语义同 `trigger_build`）。

> ⚠️ **写操作**：需先设置环境变量 `JENKINS_MCP_ALLOW_WRITE=1`，否则直接返回拒绝信息且不触发任何构建（见 §7.3）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |
| `jenkins_url` | `str` | 否 | Jenkins 服务器地址（直连模式，优先级高于 `config_path`） |
| `jenkins_token` | `str` | 否 | Jenkins API Token（直连模式） |
| `jenkins_username` | `str` | 否 | Jenkins 用户名（直连模式，默认 `admin`） |
| `history_file` | `str` | 否 | 历史文件路径（直连模式，默认为项目根目录下的 `data/build_history.json`） |

**返回值：** `dict` — 格式同 `trigger_build`（`triggered` / `failed` 两个列表，整体失败时顶层追加 `error_code` / `config_path` / `next_steps` / `docs`）。

---

#### `save_config`

> ⚠️ **危险操作**：该工具会直接**覆写用户配置文件**，调用前请确认目标路径正确，建议先备份配置文件。另请注意：`save_config` 为计划外扩展工具（原设计仅提供只读查询与构建触发），如不需要可在 MCP 客户端侧禁用。

将当前配置保存到 YAML 文件。**仅支持保存到 `.yaml` / `.yml` 路径**，非 YAML 路径会返回 `仅支持保存为 YAML 格式，当前路径: ...` 错误且不写入文件（避免覆写 JSON 等其他格式的配置导致文件损坏）。

用途是把旧格式（如顶层 `branch`、`git_param`）**规范化**为当前 `params` 结构；写回时**注释与原有排版不会保留**，因此对已是新格式的配置调用它没有收益，只有风险。

安全措施：

- 需先设置环境变量 `JENKINS_MCP_ALLOW_WRITE=1`，否则直接返回拒绝信息且不写入（见 §7.3）
- 覆写前自动生成同名 `.bak` 备份（返回值的 `backup` 字段给出备份路径）
- 写入本身为「临时文件 + 原子替换」，中断不会留下被截断的半个配置文件

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `config_path` | `str` | 否 | 配置文件路径，为空时自动检测 |

**返回值：** `dict` — 成功时包含 `message`（`配置已保存`）、`path`（文件路径）和 `backup`（备份路径，原文件不存在时为空字符串；`.bak` 已存在时退化为 `<名字>.<时间戳>.bak`，与 `init_config` 共用 `backup_config_file()`）；失败时返回统一失败载荷（顶层 `error_code` / `error` / `config_path` / `next_steps` / `docs`），未开写门控为 `write_not_allowed`、非 YAML 路径为 `invalid_target`、落点挂在过宽宿主目录下（如 `<盘符根>/.jenkins-config/`）为 `config_path_denied`（与 `init_config` 共用 `write_target_denied()`）。

---

#### `init_config`

在用户级目录或当前工作目录的 `.jenkins-config/` 下生成配置模板，供"什么都没配"的场景起步（零配置流程见 §3.9）。模板中的 `server.url` / `server.token` **恒为占位符**，绝不从环境变量或其它配置文件推断真实凭据。

参数：

- `target`（`str`，否）— 写入位置，默认 `user`。**只接受 `user` / `cwd` 两个枚举值，不接受任意路径**：路径参数由调用方可控，等于把"往哪写文件"的决定权交出去
  - `user` → `~/.jenkins-config/jenkins-config.yaml`
  - `cwd` → `<MCP Server 进程当前工作目录>/.jenkins-config/jenkins-config.yaml`；落在点目录里而非目录顶层，是为了与用户级目录同构（配置、`data/`、`.bak` 收在一处），且该子目录是探测链首位，生成即可被读到
- `overwrite`（`bool`，否）— 默认 `false`；为 `true` 时允许覆盖已存在的配置（需先开写门控）
- `format`（`str`，否）— 模板格式，本版本仅支持 `yaml`。取值在**任何写入之前**校验（忽略大小写与首尾空格），非法取值直接返回 `invalid_target` 且不落任何文件——否则 `format='json'` 会把 JSON 内容写进 `jenkins-config.yaml`


**分级写门控**（与其它写类工具不同，务必看清）：

- 目标文件不存在 + `overwrite=false`（默认）→ **直接创建，不需要** `JENKINS_MCP_ALLOW_WRITE`。门控保护的是既有资产，而这里的目标尚不存在；强制门控会让零配置用户从 1 步变 3 步（改 `mcp.json` → 重启客户端 → 再调用），最短上手路径直接失效
- 目标文件已存在 + `overwrite=false` → 一律返回 `error_code: config_exists` 且**不做任何改动**，与门控状态无关。默认调用永远不可能损坏已有配置
- 目标文件不存在，但生成后会**顶掉**另一份已生效的配置（典型场景：同目录顶层已有填好真实 token 的 `jenkins-config.yaml`，而点目录排在它前面）→ 同样返回 `config_exists`，`error` 中给出被遮蔽的路径。文件虽未被改动，但再没有人读它，连带 `data/build_history.json` 也一起失效，与"损坏"没有区别；判定按探测顺序比优先级，因此 `target='user'`（末位）不会因项目级配置存在而被拦
- `overwrite=true` → 视为改动既有资产，**必须** `JENKINS_MCP_ALLOW_WRITE=1`，否则返回 `write_not_allowed`。放行后在**同一个** `file_lock(required=True)` 临界区内依次完成"复查目标是否存在 → 复制为 `.bak` → `atomic_write` 落盘"（与 `save_config` 共用 `backup_config_file()`，避免 CLI 与 MCP 并发写互相截断）。`.bak` 已被占用时退化为 `<名字>.<时间戳>.bak`：固定名意味着第二次覆写会把上一份备份也换成模板，而配置里往往就是唯一一份可用凭据

- `target='cwd'` 但当前工作目录过宽（盘符 / 文件系统根）→ 返回 `config_path_denied`，并给出两条出路：改用 `target='user'`，或把目标目录追加到 `JENKINS_MCP_CONFIG_ROOTS`；若此时 HOME / USERPROFILE 也缺失（`target='user'` 同样走不通），归为 `home_unavailable`。CWD 恰为家目录时不拒绝：此时目标就是 `~/.jenkins-config`，与 `target='user'` 完全重合。该判定（`utils.write_target_denied()`）是**写入落点**策略，`save_config` 也调用同一个函数，避免"init 拒写、save 放行"两条不一致的写边界

**返回值：** `dict`

- 成功：`created: true`、`path`（绝对路径）、`format`、`backup`（未覆盖时为空串）、`shadowed_path`（被本次生成顶掉的配置路径，无则为空串）、`template_fields`（字段清单，每项含 `key` / `description` / `required`，与 CLI 的 `--init` 说明同源）、`next_steps`（填字段 → 调 `doctor` → 调 `list_environments`）
- 失败：`created: false`、`path`，以及统一失败载荷的五个字段。可能的错误码为 `invalid_target`（`target` / `format` 非法）、`home_unavailable`（`target='user'` 但 HOME / USERPROFILE 缺失）、`config_path_denied`、`config_exists`、`write_not_allowed`、`config_permission_denied`（目录不可写 / 锁等待超时）

生成后必须由用户本人把 `server.url`、`server.token` 改为真实取值——token 属于凭据，不应由客户端代填或猜测。

---

## 5. Resources

MCP Resources 提供只读数据端点，客户端可通过 URI 直接访问配置和历史数据。

| URI | 说明 | 返回格式 |
|-----|------|----------|
| `config://environments` | 获取所有环境信息 | JSON 数组，每项含 `name`、`description` |
| `config://projects/{env}` | 获取指定环境的项目列表 | JSON 数组，每项含 `environment`、`name`、`path` |
| `history://recent` | 获取最近 10 条构建记录 | JSON 数组，每项含 `timestamp`、`env`、`job_key`、`build_num`、`status`、`duration`、`project_name`；历史文件不存在时返回含 `message` 的提示 |
| `history://stats` | 获取构建统计摘要 | JSON 对象，含 `total`、`success`、`failure`、`building`、`other`、`success_rate`；历史文件不存在时返回含 `message` 的提示 |


所有 Resource 的读取异常都会被捕获，以 JSON `{"error": "..."}` 返回，不会向客户端抛出原始异常；只读访问不会创建历史文件或目录。

历史类资源的路径与 `show_history` 工具一致：锚定到配置文件所在目录的 `data/build_history.json`。

Resources 仍为 4 个，**没有** `config://location` 之类的"配置来源"资源：它的字段会与 `where_config` 完全重叠，而 Resource 的 URI 模板无法传 `config_path`（只能报自动探测的结果），两处并存必然随时间漂移。查配置来源统一用 `where_config`。

---

## 6. Prompts

MCP Prompts 提供预定义的交互流程模板，帮助 AI Agent 引导用户完成常见操作。

### `build_workflow`

引导用户完成构建的完整交互流程（无参数）：

1. 查看可用环境（`list_environments`）
2. 列出目标环境的项目（`list_projects`）
3. 确认项目、分支和参数
4. 触发构建（`trigger_build`）
5. 查询构建进度（`get_build_status`）
6. 失败时获取日志并分析（`get_build_log`）

### `diagnose_failure`

分析构建失败日志并给出修复建议。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `build_log` | `str` | 是 | 构建日志内容 |

分析维度：
1. 错误类型（编译错误、依赖问题、配置错误、网络问题等）
2. 具体的错误信息和位置
3. 可能的修复方案
4. 预防措施

### `setup_workflow`

引导用户从零完成本地配置直到 `list_environments` 成功的交互流程（无参数），五步：

1. 调 `doctor` 做本地体检，确认卡在哪一层（配置是否存在、是否填完、写开关状态）
2. 调 `where_config` 查看候选目录顺序与本次会读到的路径
3. 配置文件不存在时调 `init_config` 生成模板（默认写入 `~/.jenkins-config`，需要放当前工作目录时传 `target='cwd'`，落点为 `<CWD>/.jenkins-config/`，记得把该目录加进 `.gitignore`）；返回 `config_exists` 说明已有配置，不要覆盖，回到第 2 步确认路径
4. **由用户本人**打开返回的 `path`，把 `server.url` / `server.token` 从占位符改成真实取值，并按 `template_fields` 中 `required` 的字段补齐 `environments`——这一步刻意停下来等人，token 属于凭据，不代填也不猜测
5. 调 `list_environments` 验证；仍失败则按返回体的 `error_code` / `next_steps` 继续处理

---

## 7. 使用约束与注意事项

### 7.1 配置文件解析规则

各工具的 `config_path` 参数为空时，由 `resolve_config_path` 自动探测，锚定规则与 CLI 对齐（唯一差异是 `JENKINS_MCP_CONFIG` 只在 MCP 侧生效）：

| 优先级 | 规则 |
|--------|------|
| 1 | 显式传入的 `config_path`（绝对路径原样使用；相对路径按候选目录锚定） |
| 2 | 环境变量 `JENKINS_MCP_CONFIG` 指向的文件，**只接受绝对路径**，相对值记 warning 后忽略；仅 MCP Server 生效 |
| 3（源码模式） | 依次探测 项目根/.jenkins-config → 项目根 → CWD/.jenkins-config → CWD → 用户级配置目录 |
| 3（EXE 冻结模式） | 依次探测 CWD/.jenkins-config → CWD → exe 目录/.jenkins-config → exe 目录 → 用户级配置目录 |
| 支持文件名 | `jenkins-config.yaml` / `jenkins-config.yml` / `jenkins-config.json`（与 CLI 一致） |
| 均未找到 | 返回首个候选目录（源码模式为 `项目根/.jenkins-config`）下的 `jenkins-config.yaml`（后续加载时报"配置文件不存在"） |

候选目录末位固定为用户级配置目录：MCP Server 由客户端以 stdio 拉起，CWD 可能是 `/` 或家目录，只靠项目根 / CWD / exe 目录探测不可靠。

每个目录的 `.jenkins-config/` 子目录排在该目录本身之前：子目录只可能是 `init_config` / CLI `--init` 显式创建出来的，而目录顶层那份可能只是历史遗留；这样项目级布局（`<项目根>/.jenkins-config/` 里放配置 + `data/`）与用户级 `~/.jenkins-config/` 完全同构，加 `.gitignore` 也只需一条。顶层候选一并保留，既有部署不受影响。

因此：

- AI 客户端以任意工作目录拉起 MCP Server 时，只要探测链上有一份配置文件即可正确读写同一份配置；
- 多个候选目录同时存在配置文件时按上表顺序取第一个；要指定其他路径，用 `JENKINS_MCP_CONFIG`（一次性设定）或在每次调用工具时显式传 `config_path`。

调用方传入的 `config_path` 还受白名单约束：必须落在 `paths.search_bases()` 之内，或恰好是 `JENKINS_MCP_CONFIG` 指向的那个文件（该变量由部署方设定，属可信来源；其父目录**不**整树放行）。越界直接抛 `PermissionError`，可用 `JENKINS_MCP_CONFIG_ROOTS` 扩展。

历史文件路径（`trigger_build` / `rebuild_last` / `show_history` 等使用）默认锚定到**配置文件所在目录**的 `data/build_history.json`；配置来自用户级配置目录时改锚到用户级数据目录，详见 §7.4。

### 7.2 历史文件并发写入

`data/build_history.json` 由 MCP Server 与 CLI 共享，写入已做以下保护：

- **跨进程文件锁**：写入前对同目录的 `build_history.json.lock` 加排他锁（Windows 用 `msvcrt.locking`，POSIX 用 `fcntl.flock`），最长等待 10 秒；无法创建锁文件时降级为不加锁写入
- **原子替换**：先写 `build_history.json.<pid>.tmp`（文件名含进程号，避免多进程写同一临时文件）并 `fsync`，再 `os.replace` 覆盖目标文件，写入中断不会留下被截断的 JSON
- **损坏文件保护**：读取到无法解析的 JSON 时，先把原文件另存为 `build_history.json.corrupt` 再按空历史继续，避免后续写入把损坏内容连带真实历史一起丢弃

配置文件的写入（`save_config`）同样为原子替换，并在覆写前生成 `.bak` 备份。

即便如此，仍建议避免长时间并行使用 MCP Server 与 CLI 执行构建：占位记录的状态语义（见 `trigger_build` 警告）在两侧混用时容易误读。

### 7.3 环境变量

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `JENKINS_MCP_ALLOW_WRITE` | 未设置（只读） | 设为 `1`/`true`/`yes`/`on` 才允许写操作（`trigger_build`、`rebuild_last`、`save_config`）；未设置时这些工具直接返回 `write_not_allowed` 载荷，其余只读工具不受影响。`init_config` 为分级门控：仅在 `overwrite=true`（覆盖已有配置）时要求该变量，创建新文件不要求（见 §4.2） |
| `JENKINS_MCP_ALLOWED_HOSTS` | 未设置 | 逗号分隔的主机白名单，用于放行 `rebuild_last` 直连模式的 `jenkins_url`。**一旦设置即为唯一权威来源**，不再叠加配置文件中的主机（否则客户端只要在自己的 CWD 放一份 `jenkins-config.yaml` 就能扩大白名单）；未设置时退回**自动探测到的**配置文件中 `server.url` 的同一 host（不采用调用方传入的 `config_path`）；两者都取不到时任何 `jenkins_url` 都会被拒绝 |
| `JENKINS_MCP_CONFIG` | 未设置 | 直接指定配置文件路径，**优先于探测规则，且只对 MCP Server 生效**（CLI 的自动探测不读它，避免为客户端导出该变量后连 `jenkins-build` 的行为一起改掉）。**只接受绝对路径**：相对值仍受 CWD 影响，会记一条 warning 后按未设置处理。该变量由部署方设定，属可信来源，其指向的**那一个文件**自动放行；父目录不整树进入白名单 |
| `JENKINS_MCP_CONFIG_ROOTS` | 未设置 | `os.pathsep` 分隔的目录白名单，用于扩展允许读写的配置文件根目录。默认只允许 `paths.search_bases()`（项目根 / CWD / exe 目录，各含其 `.jenkins-config` 子目录 / 用户级配置目录）之内的路径；调用方传入的 `config_path` 解析后若落在白名单之外，直接抛 `PermissionError`，避免用自带配置绕过主机白名单或让 `save_config` 覆写任意 YAML |
| `JENKINS_MCP_LOG_LEVEL` | `WARNING` | 根 logger 级别，取值限于 `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`；其它值一律退回 `WARNING` |
| `JENKINS_MCP_LOG_FILE` | 未设置（只输出 stderr） | 文件日志路径；设为 `auto`（或 `1`/`true`/`yes`/`on`）时落到用户级日志目录的 `jenkins-config-mcp.<pid>.log`——每个客户端各自拉起一个 Server 进程，文件名带进程号才能避免多进程共写同一文件时轮转失败。轮转策略 1 MB × 3 份；目录不可写时降级为仅 stderr，不影响启动 |


推荐部署方式：默认不设 `JENKINS_MCP_ALLOW_WRITE`，让 MCP Server 处于只读模式；需要 AI 代为触发构建时，再在客户端的 server 配置中显式注入该变量。

### 7.4 配置与数据文件的放置位置


stdout 是 JSON-RPC 通道，**日志一律走 stderr**，客户端会自行落盘（如 Claude Desktop 的 `~/Library/Logs/Claude/mcp-server-*.log`、Windows `%APPDATA%\Claude\logs\`）；只有显式设置 `JENKINS_MCP_LOG_FILE` 时才额外写文件。

用户级目录三平台统一为 `~/.jenkins-config`（`jenkins_config/paths.py` 中的 `user_config_dir()` / `user_log_dir()`）：

- 配置：`~/.jenkins-config/jenkins-config.yaml`
- 数据（构建历史）：`~/.jenkins-config/data/build_history.json`
- 日志：`~/.jenkins-config/logs/`

按平台分散到 `%LOCALAPPDATA%` / `~/Library/Application Support` / `~/.config` 更合系统惯例，但要三行才说得清，而且配置目录与数据目录在 Windows、macOS 上重合、只在 Linux 上分开，反而多出一类平台差异。这里选可发现性，代价是不再尊重 `XDG_CONFIG_HOME`。

配置文件解析优先级：显式参数 `config_path` → `JENKINS_MCP_CONFIG`（仅 MCP，绝对路径）→ 候选目录探测（源码模式 `项目根/.jenkins-config → 项目根 → CWD/.jenkins-config → CWD → 用户配置目录`，EXE 模式把前两组换成 `CWD` 与 `exe 目录`）。

构建历史统一写在配置文件同级的 `data/build_history.json`。走 npx 时配置在 `~/.jenkins-config/`，历史就落在 `~/.jenkins-config/data/`，与带版本号的 npx 缓存目录（`~/.cache/jenkins-config-mcp/<tag>/`）无关，升级换目录不会丢历史。

### 7.5 统一失败载荷与错误码

所有工具的失败返回都带同一组字段（定义在 `jenkins_config/mcp/errors.py`）：

- `error_code` — 闭集错误码（见下），供客户端做分支判断
- `error` — 人类可读的错误描述
- `config_path` — 相关配置文件的绝对路径，未解析出来时为空串
- `next_steps` — 可执行动作列表，**恒不为空**（未显式指定时按错误码取默认动作）。每条都是"调某个 tool / 设某个环境变量 / 改某个文件"，不写"请检查配置"这类无从下手的描述
- `docs` — 文档位置，固定为 `docs/mcp/README.md`

载荷放在哪里按返回类型分（见 §4 开头）：dict 型合并到顶层，list 型返回单元素纯错误载荷，`trigger_build` / `rebuild_last` 保持容器结构、四字段追加在顶层。

错误码枚举、来源异常与 `next_steps` 方向：

- `config_not_found` — 来源 `FileNotFoundError`（探测链上没有配置文件，或显式路径不存在）。方向：调 `where_config` 看候选顺序 → 调 `init_config` 生成模板 → 或设 `JENKINS_MCP_CONFIG` 绝对路径
- `config_parse_error` — 来源 `yaml.YAMLError`、非校验类 `ValueError`（YAML/JSON 语法坏、顶层不是字典）。方向：调 `where_config` 确认在读哪个文件 → 逐行查语法 → 调 `doctor` 复查
- `config_path_denied` — 来源 `resolve` 阶段的 `PermissionError`（路径落在白名单之外）。方向：改用 `allowed_config_bases` 之内的路径 → 或把该目录追加到 `JENKINS_MCP_CONFIG_ROOTS`
- `config_permission_denied` — 来源 `read` 阶段的 `OSError`（含 Windows 对目录抛的 `PermissionError` 与 Linux 的 `IsADirectoryError`，以及 `init_config` 写入失败 / 锁超时）。方向：确认是文件而非目录 → 补齐读（写）权限后调 `doctor` 复查
- `config_incomplete` — 来源以 `配置错误: ` 开头的 `ValueError`、项目缺 `name` 的 `KeyError`，以及占位符未替换。方向：把 `server.url` / `server.token` 改为真实取值 → 调 `doctor` 确认 `config_complete` 变 ok
- `home_unavailable` — 来源 `RuntimeError`（HOME / USERPROFILE 均缺失，`Path.home()` 失败）。方向：设置 HOME（Windows 为 USERPROFILE）→ 或用 `JENKINS_MCP_CONFIG` 绕开用户级目录（`init_config` 另给出 `target='cwd'`）
- `config_exists` — `init_config` 目标已存在且 `overwrite=false`（未做任何改动），或目标虽不存在但生成后会顶掉另一份已生效的配置（`error` 里给出被遮蔽的路径）。方向：调 `where_config` 看现有配置 → 直接编辑它 → 确需覆盖 / 改用新位置时传 `overwrite=true` 并先开写门控
- `write_not_allowed` — 未设 `JENKINS_MCP_ALLOW_WRITE`（`trigger_build` / `rebuild_last` / `save_config`，以及 `init_config` 的覆盖分支）。方向：在客户端 `env` 中设 `JENKINS_MCP_ALLOW_WRITE=1` → 重启 Server 后重试
- `invalid_target` — 入参取值不合法：环境名为空、环境下无匹配项目、`params` 解析失败、`jenkins_url` 不在主机白名单、历史无可重建记录、`save_config` 非 YAML 路径、`init_config` 的 `target` / `format` 非法。方向：先用 `list_environments` / `list_projects` / `show_history` 确认可用取值，再用确认后的取值重调
- `unknown_error` — 兜底码，给不属于上面任何一类的失败（历史文件损坏、Jenkins 网络异常等）。方向：调 `doctor` 拿完整体检 → 看 stderr 日志（可设 `JENKINS_MCP_LOG_LEVEL=DEBUG`）

分类维度是"**用户下一步该做什么**"，不是"底层抛了什么异常"：`config_path_denied` 与 `config_permission_denied` 的底层异常同为 `PermissionError`，但前者要改 `JENKINS_MCP_CONFIG_ROOTS`、后者要改文件权限，因此判别依据是异常发生的阶段（`resolve` / `read` / `parse` / `validate`）而非异常类型。Jenkins 连接失败也刻意不套用这套分类（`requests` 的连接异常是 `OSError` 子类，会被误归成"补齐文件读权限"），而是单独给网络方向的 `next_steps`。



---


## 8. 测试

MCP 相关测试位于 `tests/test_mcp/` 目录，覆盖：

- `test_server.py` — FastMCP 实例（惰性单例）、`_register_tools()` 注册全部 14 个工具、`current_log_sinks()`、`main()` 入口行为
- `test_config_tools.py` / `test_build_tools.py` / `test_history_tools.py` / `test_diagnose_tools.py` — 各工具模块的参数解析、返回结构与异常处理（含统一失败载荷的形状）
- `test_where_tools.py` / `test_doctor_tools.py` / `test_init_tools.py` — `where_config` 的来源判定与候选明细、`doctor` 的 11 项检查与判级、`init_config` 的分级写门控
- `test_errors.py` — 错误码闭集、`failure_payload()` 的字段完整性、`classify()` 的阶段判别

配置锚定本身的测试在 `tests/test_paths.py`（`search_bases_detail()` / `probe_report()` 的顺序与跳过原因）。

运行方式：

```bash
# 运行 MCP 测试目录
uv run pytest tests/test_mcp -v

# 运行全部测试（含 MCP）
uv run pytest tests/ -v
```

**依赖缺失时自动跳过**：`tests/test_mcp/conftest.py` 通过 `pytest.importorskip("mcp")` 实现跳过机制——在未安装 `mcp` extra 的环境中（如仅执行 `uv sync --extra dev`），整个 `tests/test_mcp/` 目录会被优雅跳过，而非报 `ModuleNotFoundError`。

---

## 9. 调试

### 使用 MCP Inspector

MCP Inspector 是官方提供的调试工具，可以交互式测试 Tools、Resources 和 Prompts：

```bash
# 安装 mcp 依赖（如尚未安装）
uv sync --extra mcp

# 启动 Inspector
uv run mcp dev jenkins_config/mcp/server.py
```

### 常见问题排查

| 问题 | 可能原因 | 解决方案 |
|------|----------|----------|
| 客户端里根本看不到这个 server | 配置写到了客户端不读的文件（如给 Claude Code 写了 `claude_desktop_config.json`） | 按 §3.4 确认配置位置；Claude Code 用 `claude mcp list` 验证是否登记成功 |
| 登记成功但当前会话看不到 | MCP 配置只在启动时读取 | 重启客户端（Claude Desktop 需完全退出，Windows 从托盘退出） |
| `env` 里的变量像没生效 | 在 shell 里 `export` 了，没写进 server 配置的 `env` | 见 §3.3；Inspector 用 Environment Variables 面板 |
| 加了 `env` 后 server 起不来 | 客户端把自定义 `env` 当替换而非合并，`PATH` 丢了 | 在 `env` 里一并显式给出 `PATH` |
| 启动即退出并提示缺少依赖 | 未安装 `mcp` extra | 执行 `uv sync --extra mcp` 或 `pip install "jenkins-config[mcp]"` |
| 连接失败 | Jenkins 服务器不可达 | 检查网络和 `jenkins-config.yaml` 中的服务器地址 |
| 配置文件不存在 | 未创建配置文件 | 调 `init_config` 生成模板（见 §3.9），或运行 `jenkins-build --init` |
| 不确定读的是哪份配置 | 探测链上有多份配置文件 | 调 `where_config` 看 `config_path` 与 `source`；要固定一份用 `JENKINS_MCP_CONFIG` |
| 说不清哪一步坏了 | 配置 / 权限 / 日志任一层出问题 | 调 `doctor`（默认不发网络请求），按 `checks` 里第一个 `error` 项的 `hint` 处理 |
| `init_config` 返回 `config_exists` | 目标已有配置，默认不覆盖 | 直接编辑现有文件；确需覆盖时开 `JENKINS_MCP_ALLOW_WRITE=1` 并传 `overwrite=true` |
| `list_environments` 返回一条带 `error_code` 的记录 | 配置加载失败（list 型工具的失败载荷） | 按该元素的 `next_steps` 处理，不要把它当成一个叫 error 的环境 |
| Tool 调用报错 | 参数格式错误 | 检查参数类型和必填项 |
| `trigger_build` 返回 `build_num: null` | Jenkins 队列静默期内编号未分配 | 正常现象，稍后用 `get_build_status` 查询；该记录 `build_num=0` 不参与重建分组 |
| 返回 `error_code: write_not_allowed` | 未开启写开关 | 在 MCP 客户端的 server 配置中设置 `JENKINS_MCP_ALLOW_WRITE=1` 并重启客户端（见 §7.3） |
| `rebuild_last` 返回地址不被允许 | 直连模式的 `jenkins_url` 不在白名单 | 用配置文件模式，或在 `JENKINS_MCP_ALLOWED_HOSTS` 中加入该主机 |
| `get_build_log` 开头出现「日志已截断」 | 日志超过 `tail_kb`（默认 50KB） | 正常现象，只保留尾部；需要更多内容时增大 `tail_kb` |
| 历史统计成功率偏低 | `BUILDING` 占位记录未落终态 | 占位记录已不参与成功率分母，可用 `building` 字段确认数量；真实状态用 `get_build_status` 查询 |
| 出现 `build_history.json.corrupt` | 历史文件曾被写坏并已自动备份 | 该文件是损坏内容的留存副本，可人工修复后合并回历史，或直接删除 |
| 历史为空 | 从未执行过构建 | 先通过 `trigger_build` 触发构建 |
