# Changelog

## Unreleased

### ✨ 新特性

- **YAML 配置文件支持** — 配置文件支持 `.yaml` / `.yml` 格式，支持注释。推荐新项目使用 YAML。
- **动态参数系统** — 所有 Jenkins 构建参数通过 `params: dict` 传递，移除硬编码的 `branch`、`git_param`、`default_branch` 字段。新增插件参数只需在配置文件中添加键值对，无需修改代码。
- **branch_field 配置** — 新增 `branch_field` 配置项，用于指定 CLI `-b/--branch` 覆写哪个参数键（默认: `branch`）。

### 🔧 改进

- `Config.load()` 自动检测文件格式（`.yaml`/`.yml` → YAML，`.json` → JSON）
- JSON 配置文件向后兼容，旧格式仍可加载（自动迁移弃用字段到 `params`）
- 模块拆分（`config_types.py` / `config_io.py` / `config.py` / `cmd_*.py`），单文件不超过 500 行
- CLI 模块化重构 — 按功能拆分为 `cmd_build`、`cmd_init`、`cmd_interactive`、`cmd_list`

### ⚠️ 弃用

以下字段仍可加载，但会在日志中输出迁移警告，后续版本将移除：

| 弃用字段 | 替代方式 |
|---------|---------|
| `branch` (Project) | `params: {BRANCH: value}` |
| `git_param` (Project) | 顶层 `branch_field` 或环境级 `branch_field` |
| `default_branch` (Environment) | `params: {branch: value}` |
| `git_param` (Environment) | 环境级 `branch_field` |
| `params` 字符串格式 | `params` 字典格式 |

### 迁移指南

旧格式（JSON）：

```json
{
  "dev": {
    "default_branch": "develop",
    "git_param": "GIT_BRANCH",
    "projects": [
      {"name": "my-app", "branch": "feature", "git_param": "BRANCH"}
    ]
  }
}
```

新格式（YAML）：

```yaml
dev:
  branch_field: GIT_BRANCH
  params:
    GIT_BRANCH: develop
  projects:
    - name: my-app
      params:
        BRANCH: feature
```

迁移步骤：

1. 复制旧配置到备份
2. 将 `default_branch` 值移到 `params` 中
3. 将 `git_param` 值移到 `branch_field`
4. 将 `branch` 值移到 `params`
5. 将 `params` 字符串格式改为字典格式
6. 重命名配置文件为 `.yaml`（可选）

### 📦 依赖变更

- 新增: `pyyaml>=6.0`

---

## v1.1.5 (2026-06-09)

### 🔧 改进

- 更新 `.gitignore` 和 `CLAUDE.md` 文档内容

---

## v1.1.4 (2026-05-12)

### ✨ 新特性

- 启动脚本预先输出环境准备提示，消除 `-i` 交互模式下的空白等待期

### 🔧 改进

- 预加载 `prompt_toolkit` 以加快交互模式启动速度

---

## v1.1.3 (2026-05-08)

> **注**: 与 v1.1.2 为同一提交的重复打标，无额外变更。

---

## v1.1.2 (2026-05-08)

### 🔧 改进

- 添加应用程序图标支持（`pillow` 依赖）
- 打包脚本默认图标路径修复: `assets/icon.ico` → `assets/logo.ico`

---

## v1.1.1 (2026-05-08)

### 🐛 修复

- `--init -i` 交互模式缺少函数定义导致 `NameError`

### ✨ 新特性

- 打包脚本 `build.py` 新增 `--icon` 参数，支持自定义 exe 图标

### 🔧 改进

- 同步更新 README.md

---

## v1.1.0 (2026-05-07)

### ✨ 新特性

- 新增 `--init` 初始化模板指令，支持静默生成和交互式引导
- `--init -i` 交互引导模式，通过 `questionary` 逐步配置 Jenkins 服务器、构建行为、环境和项目

---

## v1.0.4 (2026-05-11)

### 🔧 改进

- 预加载 `prompt_toolkit` 以加快交互模式启动速度（cherry-pick 到 v1.0.x 分支）

---

## v1.0.2 (2026-05-07)

### 🐛 修复

- Release Assets 多平台二进制文件名冲突导致互相覆盖

---

## v1.0.1 (2026-05-07)

### 🔧 改进

- 打标签时自动创建 GitHub Release 并上传构建产物

---

## v1.0.0 (2026-05-07)

### ✨ 初始版本

- Jenkins 自动构建工具
- JSON 配置文件，支持多环境/多项目
- 交互式构建选择，并行/顺序构建
- 构建历史记录和统计
