# Jenkins 自动构建脚本 - JSON 配置支持

## ✅ 已完成的功能

### 1. 配置文件
- `jenkins-config.json` - 配置文件模板
- `jenkins-config.example.json` - 完整示例配置
- `load-config.py` - Python 配置解析器（使用 uv 运行）

### 2. 使用方法

#### 基本使用
```bash
cd jenkins-config

# 构建 dev 环境所有项目
./jenkins-auto-build.sh --env dev

# 构建特定项目（新格式）
./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web,dev:pms-order-web

# 构建特定项目（旧格式，向后兼容）
./jenkins-auto-build.sh --jobs dev,test
```

#### 列出功能
```bash
# 列出所有环境
./jenkins-auto-build.sh --list-envs

# 列出指定环境的项目
./jenkins-auto-build.sh --list-projects dev
```

#### 使用配置文件
```bash
# 使用自定义配置文件
./jenkins-auto-build.sh --config my-config.json --env dev

# 带参数构建
./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web --params "skip_tests=true"
```

### 3. 配置文件格式

```json
{
  "server": {
    "url": "http://192.168.0.205:28090",
    "token": "1131b7e624c5aec80a2437440115b34187"
  },
  "build": {
    "mode": "parallel",
    "poll_interval": 10,
    "build_timeout": 3600,
    "curl_timeout": 30,
    "log_dir": "./jenkins_logs"
  },
  "environments": {
    "dev": {
      "default_branch": "develop",
      "params": "skip_tests=false",
      "projects": [
        {
          "name": "pms-biz-plan-web",
          "branch": "develop",
          "params": "skip_tests=false"
        },
        {
          "name": "pms-order-web",
          "branch": "develop",
          "params": "skip_tests=false&optimize=true"
        }
      ]
    },
    "test": {
      "default_branch": "test",
      "params": "skip_tests=true",
      "projects": [
        {
          "name": "pms-biz-plan-web-test",
          "branch": "test"
        }
      ]
    }
  }
}
```

### 4. 命令行参数

| 参数 | 说明 | 示例 |
|-----|------|------|
| `-m, --mode` | 构建模式（parallel/sequential） | `--mode sequential` |
| `-c, --config` | 配置文件路径 | `--config my-config.json` |
| `-e, --env` | 构建指定环境 | `--env dev` |
| `-j, --jobs` | 构建特定项目 | `--jobs dev:pms-biz-plan-web` |
| `-p, --params` | 自定义参数 | `--params "skip_tests=true"` |
| `--list-envs` | 列出所有环境 | `--list-envs` |
| `--list-projects` | 列出环境项目 | `--list-projects dev` |
| `-d, --debug` | 调试模式 | `--debug` |
| `-h, --help` | 显示帮助 | `--help` |

### 5. 项目配置格式

**新格式（推荐）：**
- 格式：`env:project`
- 示例：`dev:pms-biz-plan-web`
- 多个项目：`dev:pms-biz-plan-web,dev:pms-order-web`

**旧格式（向后兼容）：**
- 格式：`env`
- 示例：`dev,test`
- 仍然可用，但功能有限

### 6. 参数合并规则

优先级（高到低）：
1. 命令行参数（`--params`）
2. 项目特定参数（`projects[].params`）
3. 环境默认参数（`environments.xxx.params`）
4. 全局默认参数（日期、分支）

### 7. 测试配置解析器

```bash
cd jenkins-config

# 测试解析 dev 环境
uv run python load-config.py jenkins-config.example.json dev

# 测试解析所有环境
uv run python load-config.py jenkins-config.example.json

# 运行测试脚本
cd test
uv run python test-config.py
```

### 8. 文件结构

```
jenkins-config/
├── jenkins-config.json              # 配置文件模板
├── jenkins-config.example.json      # 完整示例
├── load-config.py                   # Python 解析器
├── jenkins-auto-build.sh           # 主脚本
├── README.md                        # 文档
└── test/
    └── test-config.py              # 测试脚本
```

## ⚠️ 注意事项

1. **Python 依赖**：需要安装 Python 或 uv 来解析 JSON 配置
2. **向后兼容**：旧的 `--jobs dev` 格式仍然支持
3. **配置文件**：如果配置文件不存在，使用内置默认配置
4. **Job Key 格式**：新格式为 `env-project_name`（如 `dev-pms_biz_plan_web`）

## 📝 快速开始

```bash
cd jenkins-config

# 1. 查看所有可用环境
./jenkins-auto-build.sh --list-envs

# 2. 构建 dev 环境所有项目
./jenkins-auto-build.sh --env dev

# 3. 构建特定项目
./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web

# 4. 查看帮助
./jenkins-auto-build.sh --help
```
