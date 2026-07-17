# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=jenkins_config --cov-report=term-missing -v

# Run single test file
uv run pytest tests/test_builder.py -v

# Run single test
uv run pytest tests/test_config.py::test_save_yaml -v

# Run CLI directly (uses jenkins-config.yaml by default)
uv run python -m jenkins_config.cli --help

# Use shell wrapper (macOS/Linux)
./jenkins-auto-build.sh -i                  # Interactive mode
./jenkins-auto-build.sh -e dev -b hotfix    # Build with branch override
./jenkins-auto-build.sh -r                  # Rebuild last

# Use PowerShell wrapper (Windows)
./jenkins-auto-build.ps1 -i

# Package to platform binary
uv run python build.py                      # Single-file binary (~14 MB)
uv run python build.py --dir                # Directory mode (faster startup)
uv run python build.py --clean              # Clean and rebuild
```

## Architecture

```
jenkins_config/
├── cli.py                # Entry point — argparse dispatch, config path resolution
│                         # Lazily imports command modules via `from .cmd_xxx import`
├── cmd_build.py          # Build execution, rebuild-last, report generation, log cleanup
├── cmd_init.py           # Config initialization (silent template + `--init -i` guided)
├── cmd_interactive.py    # Interactive build selection (questionary UI, 4-step flow)
├── cmd_list.py           # List environments, projects, history, history stats
│
├── config_types.py       # Pure dataclasses: Config, ServerConfig, BuildConfig,
│                         # Environment, Project, Job (no I/O or logic)
├── config_io.py          # YAML/JSON loading with auto-format detection,
│                         # saving, template generation, backward compat for old JSON fields
├── config.py             # Re-exports all types; monkey-patches I/O and business
│                         # methods (get_jobs, list_environments, etc.) onto Config class
│
├── builder.py            # Builder class — build_parallel (ThreadPoolExecutor),
│                         # build_sequential, _build_single (trigger → queue → wait → log)
├── jenkins.py            # JenkinsClient — HTTP API wrapper via requests:
│                         # crumb-based CSRF, buildWithParameters, queue polling, consoleText
├── history.py            # HistoryManager — BuildRecord persistence to JSON,
│                         # list/stats/get_last_build_group/clear (max 100 records)
│
├── build_result.py       # BuildResult dataclass (job_key, build_num, status, duration, ...)
├── build_errors.py       # Error log file generation + error line extraction from console logs
├── utils.py              # ANSI-colored logging (stderr), print_header/print_sep,
│                         # format_duration, debug mode

entry_point.py            # EXE entry point (adds project root to sys.path, calls cli.main)
build.py                  # PyInstaller packaging script

tests/
├── test_config.py        # Config loading, get_jobs filtering, branch_field priority
├── test_config_io.py     # YAML/JSON I/O, save/load round-trip, template, param parsing
├── test_config_business.py  # get_jobs merge logic, create_job_from_record, backward compat
├── test_jenkins.py       # JenkinsClient mocks: crumb, trigger, queue polling, status
├── test_builder.py       # Builder with mocked JenkinsClient: success/failure/timeout paths
├── test_history.py       # BuildRecord CRUD, stats, max_records, corrupted file, clear
├── test_cli.py           # _resolve_config_path, main dispatch for all command flags
├── test_cmd_build.py     # _cleanup_old_logs, generate_report (success/mixed/unknown)
├── test_cmd_build_run.py # run_build + run_rebuild_last (split from test_cmd_build, ≤500 lines)
├── test_cmd_interactive.py  # Interactive flow with mocked questionary (14 tests)
├── test_cmd_init.py      # init: silent template, YAML/JSON example, force overwrite
├── test_cmd_list.py      # list_environments, list_projects, show_history, stats
├── test_build_errors.py  # save_error_log, extract_error_lines
├── test_utils.py         # format_duration, print_sep, debug mode
└── test_*.py             # ~17 test files total, 172 tests, all passing
```

## Key patterns

### Modular command dispatch
`cli.py` uses lazy `from .cmd_xxx import xxx` inside each `if args.xxx:` branch. This keeps startup fast and avoids circular imports. When testing `main()`, patch at the **source module** (e.g., `jenkins_config.cmd_list.list_environments`) rather than `jenkins_config.cli.list_environments` since imports are lazy.

### Config class pattern
Dataclass definitions live in `config_types.py`. I/O methods live in `config_io.py`. Business methods live in `config.py`. Both are monkey-patched onto the `Config` dataclass:
```python
# config.py
Config.load = classmethod(lambda cls, path: _load_config(path))
Config.get_jobs = _get_jobs
```
This preserves the `Config.load()` call API while keeping the source files under 500 lines each.

### Dynamic params (no hardcoded fields)
All Jenkins build parameters go through `params: dict` — no `branch`, `git_param`, or `default_branch` fields:
- `Config.branch_field: str = "branch"` — tells CLI `-b` which param key to override
- `Environment.branch_field: str = ""` — per-env override of the global branch_field
- `Job.branch` is a **derived** field (populated from `params[branch_field]` at `get_jobs()` time)
- Adding a new Jenkins plugin parameter needs zero Python code changes

**Backward compat**: Old JSON configs with `branch`, `git_param`, `default_branch` fields still load correctly with deprecation warnings. Params support both dict format (`{BRANCH: develop}`) and legacy string format (`"BRANCH=develop&skip_tests=false"`).

**Param merge priority** (in `get_jobs()`):
CLI `-p` > project `params` > env `params` (simple `dict.update()` chain)

### Config path resolution
- Source mode: resolves relative paths from the project root (`__file__.parent.parent`)
- EXE mode (PyInstaller): tries cwd first, then exe directory, falls back to cwd

### Build flow (`builder.py`)
Single Job: `trigger_build()` → `get_build_number()` (queue polling) → `_wait_for_build()` (status polling loop) → `get_build_log()` → save log → `BuildResult`

Parallel: `ThreadPoolExecutor` + `as_completed`. Sequential: simple for-loop.

### Interactive mode (`cmd_interactive.py`)
4-step questionary flow: (1) build method (by-env/by-project) → (2) project selection → (3) build mode (parallel/sequential) → (4) confirmation. Single project auto-skips step 3.

### Testing patterns
- **pytest + unittest.mock** — `Mock(spec=JenkinsClient)` for builder tests, `tmp_path` fixture for file I/O
- **questionary mocking** — patch the factory function (`questionary.select`, `.checkbox`, `.confirm`), set `.ask.return_value` or `.ask.side_effect` for multiple calls
- **Lazy import patching** — for CLI tests, patch at the source module (`jenkins_config.cmd_build.run_build`) since cli.py uses lazy imports
- **Log output assertion** — `print_header()` outputs to stderr; use `capsys.readouterr().err`
- **File encoding** — `Path.write_text()` on Windows must use `encoding="utf-8"` explicitly (GBK default)
- **500-line limit** — test files are split by topic when they exceed 500 lines

### Error handling
When a build fails to trigger or queue times out, `save_error_log()` writes a structured `.log` file with diagnostics and troubleshooting suggestions. `extract_error_lines()` searches console logs for known error keywords.

## Agent skills

### Issue tracker

Issues tracked in GitHub Issues via `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — one `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.

## Dependencies

- `requests>=2.28.0` — Jenkins HTTP API
- `questionary>=2.0.0` — interactive terminal UI (checkbox, select, confirm, text, password)
- `pyyaml>=6.0` — YAML config loading/saving (supports comments in config files)
- `pillow` — app icon processing (optional, build-time only)
- `prompt_toolkit` — transitive via questionary; imported eagerly in interactive mode for Windows startup perf
- dev: `pytest>=7.0.0`, `pytest-cov>=7.1.0`, `pyinstaller>=6.0.0`, `colorama>=0.4.6`

## Data persistence

- `data/build_history.json` — build records stored relative to config file parent. Auto-created on first use. Max 100 records.
- `jenkins_logs/` (configurable via `build.log_dir`) — build logs in `build_YYYYMMDD/` subdirectories. Old logs auto-cleaned after `log_retention_days` by `_cleanup_old_logs()`.

## Config file format

Default: `jenkins-config.yaml` (supports comments). JSON files (`.json`) still load with full backward compatibility. Example config with YAML comments at `jenkins-config.example.yaml`. Generate a template with `--init` or view the field reference with `--help-config`.
