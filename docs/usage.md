# 三种使用方式

同一套核心代码，三种交付形态。按使用场景选择：

| 方式 | 需要的环境 | 适用场景 | 详见 |
|------|-----------|---------|------|
| 1. MCP Server | Node.js 18+（npx 启动器）或 Python 3.10+ + uv（源码） | 让 AI Agent（Claude Code、Claude Desktop、Cursor 等）代为查询与触发构建 | 本文 §1、[MCP Server 文档](mcp/README.md) |
| 2. 独立可执行文件（EXE） | 无（二进制自带 Python 运行时） | 不装 Python 的机器直接用命令行；内网分发 | 本文 §2 |
| 3. 源码启动 | Python 3.10+ 与 uv | 本机开发调试、二次开发 | 本文 §3 |

三种方式读同一份 `jenkins-config.yaml`，配置文件的放置与探测规则统一见[配置文件文档](configuration.md)。

---

## 1. MCP Server（AI Agent 集成）

把构建能力暴露为 MCP 工具（共 14 个 Tools、4 个 Resources、3 个 Prompts），AI Agent 可查询环境/项目/配置、触发构建、查状态与日志、看历史、重建上次构建。**默认只读**，`trigger_build` / `rebuild_last` / `save_config` 需设置环境变量 `JENKINS_MCP_ALLOW_WRITE=1` 才放行。

### 1.1 npx 启动器（推荐，无需 Python）

启动器包 `@zythegit/jenkins-config-mcp` 首次运行时从 GitHub Release 下载当前平台的预编译二进制（自带 Python 运行时，sha256 校验后缓存复用），目标机器只需 Node.js 18+。

这段 `mcpServers` 适用于 Claude Desktop、Cursor 等大多数客户端：

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

Claude Code 用命令登记最省事：

```bash
claude mcp add jenkins-build -e JENKINS_MCP_ALLOW_WRITE=1 -- npx -y @zythegit/jenkins-config-mcp
```

注意：

- 环境变量**只能写在 server 配置的 `env` 字段里**，shell 里 `export` / `$env:` 无效（stdio 传输只传白名单变量）
- Codex CLI（TOML）、OpenCode（`environment` 键）、Pi（需先装适配器）、VS Code（顶层键是 `servers`）各有自己的格式，逐个客户端的写法见 [MCP Server 文档](mcp/README.md) §3.4
- 改完配置要重启客户端，MCP 配置不热加载
- npx / EXE 方式没有"项目目录"概念，配置文件要放 `~/.jenkins-config/`，或用 `JENKINS_MCP_CONFIG` 给绝对路径（见 [MCP Server 文档](mcp/README.md) §3.7）

### 1.2 源码方式跑 MCP Server

开发调试时，客户端里直接登记 uv 命令：

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

需要先安装 mcp 可选依赖：`uv sync --extra mcp`。调试工具链：

```bash
uv run mcp dev jenkins_config/mcp/server.py   # MCP Inspector
```

更多接入细节（工具清单、错误码、写闸门、主机白名单）见 [MCP Server 文档](mcp/README.md)。

---

## 2. 独立可执行文件（EXE）

从 [GitHub Release](https://github.com/zyTheGit/jenkins-config/releases) 下载对应平台的 `jenkins-build` 可执行文件，无需安装 Python。支持 Windows x64、macOS x64 / arm64、Linux x64 / arm64。

```bash
# 配置文件放在 exe 同级目录
jenkins-build.exe --list-envs

# 或用 -c 指定配置文件路径
jenkins-build.exe -c /path/to/config.yaml --list-envs

# 交互式选择构建
jenkins-build.exe -i

# 构建指定环境
jenkins-build.exe -e dev
```

没有配置文件时先执行 `jenkins-build.exe --init -i` 交互式生成模板。

EXE 模式的配置探测候选目录与源码模式不同（CWD 与 exe 目录代替项目根），完整规则见 [paths 探测链](mcp/README.md) §7.1 或 [配置文件文档](configuration.md)。

自行打包（通常不需要，CI 在 `v*` tag 自动构建发布）：

```bash
uv run python build.py              # 单文件模式，约 15 MB
uv run python build.py --dir        # 目录模式，启动更快
```

详见[打包文档](packaging.md)。完整命令参考见 [CLI 使用指南](cli.md)。

---

## 3. 源码启动

前置要求：Python 3.10+ 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync            # 安装依赖（MCP Server 需要额外：uv sync --extra mcp）
```

三种启动入口：

```bash
# 方式一：包装脚本（优先用 .venv 里的 Python，跳过 uv run 开销）
./jenkins-auto-build.sh --help          # macOS / Linux
./jenkins-auto-build.ps1 --help         # Windows PowerShell

# 方式二：模块入口
uv run python -m jenkins_config.cli --help

# 方式三：控制台脚本（uv sync 后可用）
uv run jenkins-build --help
```

常用命令：

```bash
uv run python -m jenkins_config.cli --init -i     # 交互式生成配置模板
uv run python -m jenkins_config.cli -i            # 交互式选择构建
uv run python -m jenkins_config.cli -e dev        # 构建指定环境
uv run python -m jenkins_config.cli --history     # 查看构建历史
```

运行测试：

```bash
uv run pytest tests/ -v
```

源码模式的配置探测从项目根开始（`项目根/.jenkins-config/` → 项目根 → CWD → 用户级 `~/.jenkins-config/`），仓库里的 `jenkins-config.yaml` 会被直接找到。完整命令参考见 [CLI 使用指南](cli.md)。
