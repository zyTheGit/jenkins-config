# 配置文件

默认使用 YAML 格式（支持注释），JSON 格式仍兼容。配置文件默认名为 `jenkins-config.yaml`，也可用 `-c` 指定路径。

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

