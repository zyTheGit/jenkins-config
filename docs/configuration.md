# 配置文件

默认使用 YAML 格式（支持注释），JSON 格式仍兼容。配置文件默认名为 `jenkins-config.yaml`，也可用 `-c` 指定路径。

用 `./jenkins-auto-build.sh --init -i` 生成模板。

## 默认读取位置

不指定路径时，按顺序逐个目录探测 `jenkins-config.yaml` → `.yml` → `.json`，命中第一个就停。每个目录都**先看其 `.jenkins-config/` 子目录，再看目录本身**：

- **源码 / pip 安装**：项目根/.jenkins-config → 项目根 → 当前工作目录/.jenkins-config → 当前工作目录 → 用户级配置目录
- **npx / 独立可执行文件**：当前工作目录/.jenkins-config → 当前工作目录 → 可执行文件所在目录（同样先看其 `.jenkins-config/`）→ 用户级配置目录

点目录排在前面，是因为它只可能是 `--init` / `init_config` 显式创建出来的，而目录顶层那份可能只是历史遗留；这样项目级布局与用户级目录结构一致。顶层位置继续支持，既有配置不用搬。

> 注意反过来的一面：某个目录里一旦出现 `.jenkins-config/jenkins-config.yaml`，同目录顶层那份就整体失效，连带它的 `data/build_history.json` 也不再被读到（历史看起来像丢了）。因此 `init_config` 在生成的文件会顶掉一份已生效配置时直接返回 `config_exists` 并回报被遮蔽的路径，只有显式 `overwrite=true` 才继续（返回体里的 `shadowed_path` 会标出来）。


用户级目录三平台统一为 `~/.jenkins-config/`（Windows 即 `%USERPROFILE%\.jenkins-config\`）：

- 配置：`~/.jenkins-config/jenkins-config.yaml`
- 构建历史：`~/.jenkins-config/data/build_history.json`
- 日志：`~/.jenkins-config/logs/`（仅 MCP Server 设了 `JENKINS_MCP_LOG_FILE=auto` 时才落盘）

不随平台变化，因此没有"配置目录和数据目录是不是同一个"这类平台差异要记；代价是不再尊重 `XDG_CONFIG_HOME` 之类的系统惯例，换来一句话就能说清配置该放哪。

**构建历史**始终落在 `<配置文件所在目录>/data/build_history.json`。

CLI 侧用 `-c` 指定其他路径：

```bash
./jenkins-auto-build.sh -c /path/to/jenkins-config.yaml --list-envs
```

MCP Server 的工作目录由客户端决定、不可控，所以那一侧另有 `JENKINS_MCP_CONFIG`（指向具体文件，仅绝对路径）与 `JENKINS_MCP_CONFIG_ROOTS`（追加允许的目录）两个变量，CLI 不读它们，见 [MCP Server 文档](mcp/README.md) §3.7 与 §7.1。

## 完整示例


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

## 字段说明

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

## branch_field 与参数体系

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

