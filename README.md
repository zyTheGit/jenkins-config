# Jenkins 自动构建工具

触发并监控 Jenkins 构建。纯 Python 实现，无 curl / jq 依赖，提供 MCP Server、CLI 与独立可执行文件三种用法。

## MCP Server（AI Agent 集成）

在 MCP 客户端里填这一段即可。启动器首次运行会从 GitHub Release 下载当前平台的预编译二进制（自带 Python 运行时，sha256 校验后缓存复用），所以目标机器只需 Node.js 18+：

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

上面这段 `mcpServers` 格式适用于 Claude Desktop、Cursor 等大多数客户端。其余客户端各有自己的配置文件与键名，见下。

### 各客户端登记方式

**Claude Code** — 命令行登记最省事，`-e` 注入环境变量：

```bash
claude mcp add jenkins-build -e JENKINS_MCP_ALLOW_WRITE=1 -- npx -y @zythegit/jenkins-config-mcp
```

**Codex CLI** — 配置在 `~/.codex/config.toml`，用 TOML 而非 JSON；放到受信任项目的 `.codex/config.toml` 可做项目级限定。CLI 与 IDE 扩展共用这份配置：

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

**OpenCode** — 配置在 `~/.config/opencode/opencode.json`（或项目根的 `opencode.json`）的 `mcp` 键下。注意环境变量的键名是 `environment`，**不是** `env`：

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

**Pi** — Pi 内核刻意不内置 MCP，需要先装社区适配器并重启 Pi：

```bash
pi install npm:pi-mcp-adapter
```

适配器读取标准的 `.mcp.json`（项目根）或全局 `~/.pi/agent/mcp.json`，格式与本节开头那段 `mcpServers` 一致，直接复制即可。装好后用 `/mcp` 面板查看连接状态。

### 说明

- 共 11 个工具：环境/项目/配置查询、构建触发、状态与日志查询、历史统计、重建上次构建；另有只读 Resources 与 Prompts
- 默认**只读**，`JENKINS_MCP_ALLOW_WRITE=1` 才放行触发构建等写操作。这类变量只能写在 `env` 里，shell 里 `export` 无效
- 改完配置要重启客户端，MCP 配置不热加载
- 支持 Windows x64、macOS x64 / arm64、Linux x64 / arm64

各客户端配置文件的完整路径与作用域、工具参数、故障排查见 [MCP Server 文档](docs/mcp/README.md)。

## 配置文件

MCP 与 CLI 读同一份 `jenkins-config.yaml`：

```yaml
server:
  url: "http://jenkins.example.com:8080"
  username: admin
  token: "your-api-token"

branch_field: BRANCH

environments:
  dev:
    description: 开发环境
    params:
      BRANCH: develop
    projects:
      - name: project-a
      - name: project-b
```

用 `./jenkins-auto-build.sh --init -i` 生成模板。

### 默认读取位置

不指定路径时，按顺序逐个目录探测 `jenkins-config.yaml` → `.yml` → `.json`，命中第一个就停：

- **npx / 独立可执行文件**：当前工作目录 → 可执行文件所在目录 → 用户级配置目录
- **源码 / pip 安装**：项目根 → 当前工作目录 → 用户级配置目录

MCP Server 由客户端以子进程拉起，工作目录不可控（可能是 `/` 或你的家目录），所以走 npx 时**别指望前两个目录**，请用下面两种方式之一。

用户级目录三平台统一为 `~/.jenkins-config/`（Windows 即 `%USERPROFILE%\.jenkins-config\`）：

- 配置：`~/.jenkins-config/jenkins-config.yaml`
- 构建历史：`~/.jenkins-config/data/build_history.json`
- 日志：`~/.jenkins-config/logs/`（仅在 `JENKINS_MCP_LOG_FILE=auto` 时才落盘）

### 指定配置文件

**方式一：`JENKINS_MCP_CONFIG` 指向具体文件**（推荐，只对 MCP Server 生效，不影响 CLI 的自动探测）。**只接受绝对路径**——相对路径仍然要靠工作目录锚定，等于把这个变量存在的意义又丢回去了，这类取值会记一条 warning 后按未设置处理：

```json
{
  "mcpServers": {
    "jenkins-build": {
      "command": "npx",
      "args": ["-y", "@zythegit/jenkins-config-mcp"],
      "env": {
        "JENKINS_MCP_CONFIG": "D:/work/jenkins-config.yaml",
        "JENKINS_MCP_ALLOW_WRITE": "1"
      }
    }
  }
}
```

**方式二：`JENKINS_MCP_CONFIG_ROOTS` 追加允许的目录**（`os.pathsep` 分隔，Windows 用 `;`，其余用 `:`）。调用方传入的 `config_path` 必须落在探测目录或这里列出的目录之内，否则报错——这道限制是为了防止 AI 用一份自带 `server.url` 的配置把主机白名单绕过去，或用 `save_config` 覆写任意 YAML。注意 `JENKINS_MCP_CONFIG` 指向的文件是按**精确文件**放行的，它的父目录不会整棵放开。

CLI 侧不读这两个变量，用 `-c` 指定：

```bash
./jenkins-auto-build.sh -c /path/to/jenkins-config.yaml --list-envs
```

### 生成的文件放在哪

- **构建历史**：`<配置文件所在目录>/data/build_history.json`；走 npx 时配置在 `~/.jenkins-config/`，历史就落在 `~/.jenkins-config/data/`，与带版本号的 npx 缓存目录无关，升级不会丢
- **日志**：默认只写 stderr，由 MCP 客户端自己捕获。要落盘就设 `JENKINS_MCP_LOG_FILE`（绝对路径，或 `auto` 表示用户级日志目录），`JENKINS_MCP_LOG_LEVEL` 调级别（默认 `WARNING`）

用户级目录不随平台变化，因此也没有"配置目录和数据目录是不是同一个"这类平台差异要记；代价是不再尊重 `XDG_CONFIG_HOME` 之类的系统惯例，换来的是一句话就能说清配置该放哪。

字段说明见 [配置文件文档](docs/configuration.md)。

## CLI 用法

```bash
./jenkins-auto-build.sh -i          # 交互式选择
./jenkins-auto-build.sh -e dev      # 构建指定环境
./jenkins-auto-build.sh --history   # 查看构建历史
```

也可从 [Release](https://github.com/zyTheGit/jenkins-config/releases) 下载独立可执行文件，无需 Python。

## 文档

- [MCP Server](docs/mcp/README.md) — 客户端接入、工具清单、环境变量、写开关与主机白名单
- [配置文件](docs/configuration.md) — 字段说明、`branch_field` 与参数体系
- [CLI 使用指南](docs/cli.md) — 安装、命令参考、交互流程
- [打包](docs/packaging.md) — PyInstaller 打包与 CI 发布
- [项目结构与架构](docs/architecture.md)
- [故障排除](docs/troubleshooting.md)

## License

MIT
