# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Jenkins 自动构建工具，纯 Python 实现，无 curl/jq 依赖。三种交付形态共用一套核心代码：

1. **CLI**（`python -m jenkins_config.cli` / `jenkins-auto-build.sh|.ps1` 包装脚本）
2. **MCP Server**（`jenkins_config.mcp`，FastMCP API，供 AI Agent 调用）
3. **npm 启动器**（`npm/`，`@zythegit/jenkins-config-mcp`——首次运行从 GitHub Release 下载平台预编译二进制，Node 只负责引导）

## Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=jenkins_config --cov-report=term-missing -v

# Run single test file / single test
uv run pytest tests/test_builder.py -v
uv run pytest tests/test_config.py::test_save_yaml -v

# Run CLI directly
uv run python -m jenkins_config.cli --help

# Run MCP server (requires mcp extra: uv sync --extra mcp)
uv run python -m jenkins_config.mcp.server

# Package to platform binary
uv run python build.py              # Single-file binary (~14 MB)
uv run python build.py --dir        # Directory mode (faster startup)
uv run python build.py --clean      # Clean and rebuild
```

CLAUDE.md 底部的 `--init` / `-h` 等命令行手册以 `cli.py` argparse 为准，勿凭记忆描述参数。

## Architecture

```
jenkins_config/
├── cli.py                # 入口 — argparse 分发，配置路径解析
│                         # 每个 if args.xxx 分支内延迟 `from .cmd_xxx import`
├── cmd_build.py          # 构建执行、rebuild-last、报告生成、日志清理
├── cmd_init.py           # 配置初始化（静默模板 + `--init -i` 引导）
├── cmd_interactive.py    # 交互式构建选择（questionary UI，5 步状态机）
├── cmd_list.py           # 列出环境、项目、历史、历史统计
│
├── config_types.py       # 纯 dataclass：Config, ServerConfig, BuildConfig,
│                         # Environment, Project, Job（无 I/O 无逻辑）
├── config_io.py          # YAML/JSON 加载（自动格式检测）、保存、模板生成、旧 JSON 字段兼容
├── config.py             # re-export 所有类型；把 I/O 与业务方法（get_jobs 等）
│                         # monkey-patch 到 Config 类上
│
├── paths.py              # 路径解析唯一真源 — CLI 与 MCP 都必须走此模块，
│                         # 避免两侧锚定规则漂移（源码/EXE 模式候选目录、用户级 ~/.jenkins-config）
├── filelock.py           # 并发写保护
│
├── builder.py            # Builder — build_parallel（ThreadPoolExecutor）、
│                         # build_sequential、_build_single（trigger → queue → wait → log）
├── jenkins.py            # JenkinsClient — requests HTTP API 封装：
│                         # crumb CSRF、buildWithParameters、队列轮询、consoleText
├── history.py            # HistoryManager — BuildRecord JSON 持久化、
│                         # list/stats/get_last_build_group/clear（最多 100 条）
│
├── build_result.py       # BuildResult dataclass
├── build_errors.py       # 错误日志文件生成 + 从控制台日志提取错误行
├── utils.py              # ANSI 彩色日志（stderr）、print_header/print_sep、format_duration
│
├── mcp/                  # FastMCP Server（延迟导入 mcp，未装 extra 也可导入）
│   ├── server.py         # 入口：日志只走 stderr（stdout 是 JSON-RPC 通道）
│   ├── utils.py          # 14 个 MCP Tools 共享逻辑：配置加载、错误返回载荷
│   ├── errors.py         # 错误码分类 → 可行动失败载荷
│   ├── resources.py      # Resources（配置数据）
│   └── prompts.py        # Prompts（交互模板）
│
├── entry_point.py        # EXE 入口（sys.path 加项目根，调 cli.main）
build.py                  # PyInstaller 打包脚本
npm/                      # npm 启动器（bin/ + package.json，发布为 @zythegit/jenkins-config-mcp）
```

## 关键模式

### 模块化命令分发
`cli.py` 在每个分支内延迟 `from .cmd_xxx import xxx`，启动快且避免循环导入。测试 `main()` 时必须 patch **源模块**（如 `jenkins_config.cmd_list.list_environments`），而非 `jenkins_config.cli.list_environments`。

### Config 类模式
数据类在 `config_types.py`，I/O 方法在 `config_io.py`，业务方法在 `config.py`，后两者 monkey-patch 到 Config 上：
```python
Config.load = classmethod(lambda cls, path: _load_config(path))
Config.get_jobs = _get_jobs
```
保持 `Config.load()` 调用 API 不变，同时单文件控制在 500 行内。

### 路径解析（paths.py 是唯一真源）
锚定规则：绝对路径原样用；相对路径按运行模式在候选目录中查找；未指定时按 `CONFIG_FILE_NAMES` 顺序探测。用户级目录三平台统一为 `~/.jenkins-config`。`JENKINS_MCP_CONFIG` 只对 MCP 生效，paths 模块自动探测不读它——修改路径逻辑时两侧都不要自行实现。

### 动态 params（无硬编码字段）
所有 Jenkins 构建参数走 `params: dict`，没有 `branch`/`git_param`/`default_branch` 字段：
- `Config.branch_field: str = "branch"` — 告诉 CLI `-b` 覆盖哪个 param key
- `Environment.branch_field: str = ""` — per-env 覆盖全局 branch_field
- `Job.branch` 是**派生字段**（`get_jobs()` 时从 `params[branch_field]` 填充）
- 新增 Jenkins 插件参数零代码改动

**向后兼容**：旧 JSON 配置的 `branch`/`git_param`/`default_branch` 仍可加载（带弃用警告）。params 兼容 dict 与字符串（`"KEY=val&k2=v2"`）两种格式。

**合并优先级**（`get_jobs()`）：CLI `-p` > project `params` > env `params`（`dict.update()` 链）。

### MCP Server 特殊约束
- `stdout` 是 JSON-RPC 通道，**任何** print/log 到 stdout 都会破坏协议；日志走 stderr 或 `JENKINS_MCP_LOG_FILE`
- 默认只读，`JENKINS_MCP_ALLOW_WRITE=1` 才放行写操作
- 工具失败返回可行动载荷（`mcp/errors.py` 的 `failure_payload`），不伪造业务数据
- 写入分级闸门：`init_config` 等写工具需用户确认（详见 `docs/mcp/README.md`）

### 构建流程（builder.py）
单 Job：`trigger_build()` → `get_build_number()`（队列轮询）→ `_wait_for_build()`（状态轮询）→ `get_build_log()` → 存日志 → `BuildResult`。并行用 `ThreadPoolExecutor` + `as_completed`。

### 交互模式（cmd_interactive.py）
状态机流程，每步 ESC 可回退：① 构建方式（按环境/按项目）→ ② 环境 → ③ 项目选择 → ④ 构建模式 → ⑤ 确认。单项目自动跳过第 4 步（确认处 ESC 回到第 3 步）。`_install_esc_back()` 在每个问题 `.ask()` 前向 prompt_toolkit `Application` 追加 Escape 绑定；ESC 返回 `_BACK` 哨兵，Ctrl+C/Q 或 EOF 退出。

### 测试模式
- **pytest + unittest.mock** — builder 测试用 `Mock(spec=JenkinsClient)`，文件 I/O 用 `tmp_path`
- **questionary mocking** — patch 工厂函数（`questionary.select` `.checkbox` `.confirm`），用 `.ask.return_value` / `.ask.side_effect` 模拟多次调用
- **嵌套工具测试不依赖本机配置** — 测试需自备 tmp 配置，不要读取用户机器上真实存在的 `jenkins-config.yaml`
- **日志断言** — `print_header()` 输出到 stderr，用 `capsys.readouterr().err`
- **文件编码** — Windows 上 `Path.write_text()` 必须显式 `encoding="utf-8"`（默认 GBK）
- **500 行上限** — 源文件 / 测试文件超限时按主题拆分（如 `test_cmd_build.py` 与 `test_cmd_build_run.py`）
- 当前共 ~20 个测试文件、650+ 用例；新增功能必须带测试

## 错误处理

构建触发失败或队列出队超时时，`save_error_log()` 写入结构化 `.log`（含诊断与排查建议）。`extract_error_lines()` 在控制台日志中按已知错误关键字提取关键行。

## 数据持久化

- `data/build_history.json` — 构建记录，路径相对配置文件父目录解析。首次使用自动创建，最多 100 条
- `jenkins_logs/`（可经 `build.log_dir` 配置）— 日志按 `build_YYYYMMDD/` 子目录存放，`_cleanup_old_logs()` 按 `log_retention_days` 自动清理

## 配置文件格式

默认 `jenkins-config.yaml`（支持注释）。`.json` 仍全量兼容加载。带注释示例在 `jenkins-config.example.yaml`；`--init` 生成模板，`--help-config` 查看字段说明。模板统一从 `config_io.py` 的单一来源生成。

## Agent skills

- **Issue tracker** — GitHub Issues via `gh` CLI，见 `docs/agents/issue-tracker.md`
- **Triage labels** — 五角色词汇：needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix，见 `docs/agents/triage-labels.md`
- **Domain docs** — 单上下文布局：仓库根 `CONTEXT.md` + `docs/adr/`，见 `docs/agents/domain.md`

## 依赖

- `requests>=2.28.0` — Jenkins HTTP API
- `questionary>=2.0.0` — 交互式终端 UI
- `pyyaml>=6.0` — YAML 配置读写
- `pillow` — 应用图标处理（可选，仅打包时）
- `prompt_toolkit` — questionary 传递依赖；交互模式为 Windows 启动性能会急切导入
- `mcp`（extra）— MCP Server，延迟导入
- dev: `pytest>=7.0.0`, `pytest-cov>=7.1.0`, `pyinstaller>=6.0.0`, `colorama>=0.4.6`
