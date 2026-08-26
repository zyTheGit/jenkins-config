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

Claude Code 用命令行登记更省事：

```bash
claude mcp add jenkins-build -e JENKINS_MCP_ALLOW_WRITE=1 -- npx -y @zythegit/jenkins-config-mcp
```

- 共 11 个工具：环境/项目/配置查询、构建触发、状态与日志查询、历史统计、重建上次构建；另有只读 Resources 与 Prompts
- 默认**只读**，`JENKINS_MCP_ALLOW_WRITE=1` 才放行触发构建等写操作。这类变量只能写在 `env` 里，shell 里 `export` 无效
- 改完配置要重启客户端，MCP 配置不热加载
- 支持 Windows x64、macOS x64 / arm64、Linux x64 / arm64

各客户端的配置位置与作用域、工具参数、故障排查见 [MCP Server 文档](docs/mcp/README.md)。

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

用 `./jenkins-auto-build.sh --init -i` 生成模板。走 npx 方式时没有项目目录，把配置放到用户级配置目录（Windows `%LOCALAPPDATA%\jenkins-config\`、macOS `~/Library/Application Support/jenkins-config/`、Linux `~/.config/jenkins-config/`），或用 `JENKINS_MCP_CONFIG` 指定绝对路径。

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
