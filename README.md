# Jenkins 配置文件说明

## 概述
本目录包含 Jenkins 自动构建脚本的所有文件，包括配置文件、解析器和主脚本。

## 文件说明

### 配置文件
- `jenkins-config.json` - 配置文件模板（需修改 token）
- `jenkins-config.example.json` - 完整示例配置

### 脚本文件
- `load-config.py` - Python 配置解析器
- `jenkins-auto-build.sh` - 主构建脚本

## 使用方法

### 基本使用
```bash
cd jenkins-config
./jenkins-auto-build.sh --config jenkins-config.json --env dev
```

### 列出环境
```bash
./jenkins-auto-build.sh --list-envs
```

### 列出项目
```bash
./jenkins-auto-build.sh --list-projects dev
```

### 构建特定项目
```bash
# 新格式（推荐）
./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web,dev:pms-order-web

# 旧格式（兼容）
./jenkins-auto-build.sh --jobs dev,test
```

## 配置格式

### 服务器配置
```json
{
  "server": {
    "url": "http://your-jenkins-server:8080",
    "token": "your-jenkins-token"
  }
}
```

### 构建配置
```json
{
  "build": {
    "mode": "parallel",
    "poll_interval": 10,
    "build_timeout": 3600,
    "curl_timeout": 30,
    "log_dir": "./jenkins_logs"
  }
}
```

### 环境和项目配置
```json
{
  "environments": {
    "dev": {
      "default_branch": "develop",
      "params": "skip_tests=false",
      "projects": [
        {
          "name": "pms-biz-plan-web",
          "branch": "develop",
          "params": "skip_tests=false"
        }
      ]
    }
  }
}
```

## Job Key 格式

### 新格式（推荐）
- 格式：`env:project`
- 示例：`dev:pms-biz-plan-web`
- 支持多个：`dev:pms-biz-plan-web,dev:pms-order-web`

### 旧格式（兼容）
- 格式：`env`
- 示例：`dev,test`
- 仍然可用，但功能有限

## 参数合并规则

参数合并优先级（高到低）：
1. 命令行参数（`--params`）
2. 项目特定参数（`projects[].params`）
3. 环境默认参数（`environments.xxx.params`）
4. 全局默认参数（日期、分支）

## 完整示例

```bash
# 构建 dev 环境所有项目
./jenkins-auto-build.sh --env dev

# 构建 dev 环境的特定项目
./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web,dev:pms-order-web

# 使用自定义参数构建
./jenkins-auto-build.sh --jobs dev:pms-biz-plan-web --params "skip_tests=true&notify=false"

# 列出所有环境
./jenkins-auto-build.sh --list-envs

# 列出 test 环境的项目
./jenkins-auto-build.sh --list-projects test
```

## 测试

```bash
cd test
python test-config.py
```

## 故障排除

### 配置文件不存在
如果配置文件不存在，脚本会回退到内置默认配置。

### Python 未安装
需要安装 Python 或 uv 来解析 JSON 配置。

### 环境或项目不存在
使用 `--list-envs` 和 `--list-projects` 查看可用的环境和项目。
