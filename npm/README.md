# @zythegit/jenkins-config-mcp

`jenkins-config` MCP Server 的一键启动器。**目标机器只需要 Node.js 18+，不需要 Python。**

MCP Server 本体是 Python 实现，但发布物是 PyInstaller 打出的单文件二进制（自带 Python 运行时）。
本包是一层薄启动器：首次运行时从 GitHub Release 下载当前平台的二进制、校验 sha256 并缓存，
之后直接复用；启动后以 stdio 直通方式转发 MCP 协议。

## 用法

```jsonc
{
  "mcpServers": {
    "jenkins-build": {
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"]
    }
  }
}
```

默认为只读模式，`trigger_build` / `rebuild_last` / `save_config` 会被拒绝；
确认要让 AI 代为触发构建时再加 `"env": { "JENKINS_MCP_ALLOW_WRITE": "1" }`。

支持的平台：Windows x64、macOS x64 / arm64、Linux x64 / arm64。

## 其他客户端

上面那段 `mcpServers` 适用于 Claude Desktop / Claude Code / Cursor 等大多数客户端。以下三家键名或格式不同，照抄会配了不生效：

**Codex CLI** — `~/.codex/config.toml`（或受信任项目的 `.codex/config.toml`），TOML 格式，表名带下划线：

```toml
[mcp_servers.jenkins-build]
command = "npx"
args = ["-y", "@zythegit/jenkins-config-mcp"]
env = { JENKINS_MCP_ALLOW_WRITE = "1" }
```

也可以 `codex mcp add jenkins-build --env JENKINS_MCP_ALLOW_WRITE=1 -- npx -y @zythegit/jenkins-config-mcp`，TUI 里用 `/mcp` 看连接状态。

**OpenCode** — `~/.config/opencode/opencode.json`（或项目根的 `opencode.json`）的 `mcp` 键下，`command` 是数组，环境变量键名是 `environment` 而非 `env`：

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

**Pi** — 内核不带 MCP，得先装适配器扩展并重启 Pi：

```bash
pi install npm:pi-mcp-adapter
```

之后写项目根的 `.mcp.json` 或全局 `~/.pi/agent/mcp.json`，格式与上面那段 `mcpServers` 完全一致，直接复制。装好后用 `/mcp` 面板看连接状态。

VS Code (Copilot) 的 `.vscode/mcp.json` 顶层键是 `servers` 而不是 `mcpServers`。逐客户端的完整说明见仓库内 `docs/mcp/README.md` §3.4。

改完配置都要重启客户端，MCP 配置不热加载。


## 解析优先级

1. `JENKINS_MCP_BINARY` — 直接指定二进制路径
2. `JENKINS_MCP_PYTHON` — 指定解释器，执行 `-m jenkins_config.mcp.server`（开发用）
3. 缓存中已下载的二进制
4. 从 Release 下载二进制并校验 sha256
5. 兜底：PATH 上的 `jenkins-config-mcp` / `uvx` / `python`

日志一律走 stderr，stdout 只用于 MCP 的 JSON-RPC 通道。

## 环境变量

- `JENKINS_MCP_VERSION` — 要下载的 Release tag，默认 `v<npm 包版本>`
- `JENKINS_MCP_RELEASE_BASE` — 下载地址前缀，默认 GitHub Release，可指向内网镜像
- `JENKINS_MCP_CACHE_DIR` — 缓存根目录，默认 `~/.cache`（Windows 为 `%LOCALAPPDATA%`）
- `JENKINS_MCP_SKIP_CHECKSUM=1` — 跳过 sha256 校验（不建议）
- `JENKINS_MCP_LAUNCHER_DRYRUN=1` — 只打印解析出的命令后退出，用于排查启动问题

Server 自身的环境变量（`JENKINS_MCP_ALLOW_WRITE`、`JENKINS_MCP_ALLOWED_HOSTS`、
`JENKINS_MCP_CONFIG_ROOTS`）会原样透传，说明见仓库内 `docs/mcp/README.md`。
