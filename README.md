# Jenkins 自动构建工具

触发并监控 Jenkins 构建。纯 Python 实现，无 curl / jq / node 依赖，提供 MCP Server、CLI 与独立可执行文件三种用法。

## MCP Server（AI Agent 集成）

把构建能力暴露给 AI Agent（Claude Desktop、Cursor 等）：11 个工具（环境/项目/配置查询、构建触发、状态与日志查询、历史统计、重建上次构建）、4 个只读 Resources、2 个 Prompts。

### npx 一键接入（推荐，无需 Python）

在 MCP 客户端配置里填一行即可。启动器首次运行会从 GitHub Release 下载当前平台的预编译二进制（自带 Python 运行时），校验 sha256 后缓存复用，因此目标机器只需 Node.js 18+：

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

默认**只读**。需要让 AI 代为触发构建时再加写开关：

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

排查启动问题（只打印解析出的命令，不真正启动）：

```bash
JENKINS_MCP_LAUNCHER_DRYRUN=1 npx -y @zythegit/jenkins-config-mcp
```

### 源码 / 已安装包（需要 Python）

```bash
uv sync --extra mcp
```

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

工具参数、配置路径解析规则、启动器环境变量、并发注意事项、调试与测试见 [MCP Server 完整文档](docs/mcp/README.md)。

## 配置文件

无论用 MCP 还是 CLI，都读同一份 `jenkins-config.yaml`：

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

首次使用可以生成模板：`./jenkins-auto-build.sh --init -i`。全部字段说明见 [配置文件文档](docs/configuration.md)。

## CLI 用法

```bash
# 交互式选择（推荐）
./jenkins-auto-build.sh -i

# 构建指定环境
./jenkins-auto-build.sh -e dev

# 查看构建历史
./jenkins-auto-build.sh --history
```

也可以从 [Release](https://github.com/zyTheGit/jenkins-config/releases) 下载独立可执行文件，无需 Python。完整命令参考见 [CLI 使用指南](docs/cli.md)。

## 功能特性

- **MCP Server** — 向 AI Agent 暴露构建能力，支持 `npx` 一键接入（无需 Python）
- **初始化模板** — `--init` 快速生成配置文件，支持交互式引导
- **并行/顺序构建** — 同时构建多个项目或按顺序逐个构建
- **交互式选择** — 终端界面选择要构建的环境和项目
- **构建历史** — 自动记录构建结果，支持查看统计
- **独立可执行文件** — 打包成单个二进制，无需安装 Python

## 文档

- [MCP Server](docs/mcp/README.md) — 11 个工具、4 个 Resources、2 个 Prompts、启动器环境变量、写开关与主机白名单
- [配置文件](docs/configuration.md) — 完整示例、字段说明、`branch_field` 与参数体系
- [CLI 使用指南](docs/cli.md) — 安装、基本命令、交互流程、命令参考
- [打包](docs/packaging.md) — 本地 PyInstaller 打包、CI 发布产物
- [项目结构与架构](docs/architecture.md) — 目录结构、调用关系、测试
- [故障排除](docs/troubleshooting.md) — 常见报错与处理

## License

MIT

