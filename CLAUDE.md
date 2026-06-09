# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run single test file
uv run pytest tests/test_config.py -v

# Run CLI directly
uv run python -m jenkins_config.cli --help

# Use shell wrapper (macOS/Linux)
./jenkins-auto-build.sh -i                  # Interactive mode
./jenkins-auto-build.sh --list-envs         # List environments
./jenkins-auto-build.sh -e dev              # Build dev environment

# Use PowerShell wrapper (Windows)
./jenkins-auto-build.ps1 -i                 # Interactive mode
./jenkins-auto-build.ps1 --list-envs        # List environments
./jenkins-auto-build.ps1 -e dev             # Build dev environment

# Package to platform binary
uv run python build.py                      # Single-file binary (~14 MB)
uv run python build.py --dir                # Directory mode (faster startup)
uv run python build.py --clean              # Clean and rebuild
```

## Architecture

```
jenkins_config/              # Python package — entry point via `-m` or pyproject.toml script
├── cli.py       (entry) ─┬── config.py    (Config, Job, Environment dataclasses; JSON config loader)
│                          ├── jenkins.py   (JenkinsClient, BuildStatus; HTTP API wrapper via requests)
│                          ├── builder.py   (Builder, BuildResult; parallel/sequential orchestration)
│                          ├── history.py   (HistoryManager, BuildRecord; JSON file persistence)
│                          └── utils.py     (ANSI-colored logging, debug mode, formatting)
├── __init__.py             # Empty — package marker only
entry_point.py              # EXE entry point (adds project root to sys.path, then calls cli.main)
build.py                    # PyInstaller packaging script (onefile/onedir, icon support, cleanup)

jenkins-auto-build.sh       # Thin shell wrapper: cd to project root → `uv run python -m jenkins_config.cli "$@"`
jenkins-auto-build.ps1      # Thin PowerShell wrapper: same logic, for Windows

tests/
├── test_config.py          # Config loading, get_jobs filtering, git_param priority tests
├── test_jenkins.py         # JenkinsClient mocks: crumb, trigger, queue polling, status
├── test_builder.py         # Builder with mocked JenkinsClient: single/sequential/parallel
├── test_history.py         # BuildRecord add/list/stats/max_records_limit
└── test_utils.py           # format_duration, print_sep output
```

- **cli.py**: Single entry point. Parses argparse args, dispatches to interactive mode (`-i`), list commands, history commands, init (`--init`), or `run_build()`. The `run_init()` function supports both silent template generation and interactive guided setup via `questionary`.
- **config.py**: `Config.load()` reads JSON config file. Three-level data model: `Environment` → `Project` → `Job` (merged). `get_jobs()` filters by env/project and merges params with priority: CLI `-p` > project params > env params > defaults. `Job` key format: `{env}_{project_name}` (dashes → underscores).
- **jenkins.py**: `JenkinsClient` handles crumb-based CSRF, `trigger_build()` (POST `buildWithParameters`), `get_build_number()` (queue polling — waits for executable to be allocated), `get_build_status()` (polling via `/api/json`), `get_build_log()` (via `consoleText`).
- **builder.py**: `Builder` runs jobs via `_build_single()` (trigger → poll for queue number → `_wait_for_build()` polling loop → save log). `build_parallel()` uses `ThreadPoolExecutor` with `as_completed`; `build_sequential()` is a simple for-loop.
- **history.py**: `HistoryManager` persists build records to `data/build_history.json`. Supports `get_last_build_group()` for rebuild-last feature. Max 100 records (configurable via `MAX_RECORDS`).
- **utils.py**: All logging to stderr with ANSI colors. Debug mode guarded by global `DEBUG_MODE` flag (set via `set_debug_mode()` from CLI `-d`).

## Key patterns

- **Config path resolution**: source mode uses `__file__` relative; EXE mode tries cwd first, then exe directory (checked via `sys.frozen`).
- **Param merge priority**: CLI `-p` > project params > env params > defaults. Branch priority: CLI `-b` > project `branch` > env `default_branch` > `"main"`. `git_param` (for `git-parameter-plugin`): project > env > `"branch"` default.
- **Build flow**: trigger → queue → build number → poll status → save log → return `BuildResult`.
- **Interactive mode**: Three-step flow in `run_interactive_build()` — (1) choose build method (by-env / by-project), (2) multi-select projects via `questionary.checkbox`, (3) choose parallel/sequential mode. Single-project automatically skips mode selection.
- **Init interactive mode**: `--init -i` walks through server config → build behavior → environments & projects via `questionary` prompts, writing the complete `jenkins-config.json`.
- **Error log fallback**: When build fails to trigger or queue times out, a structured `.log` file with diagnostics and troubleshooting suggestions is saved.
- **Testing pattern**: Tests use `pytest` + `unittest.mock` extensively. `tmp_path` fixture for config/history file tests. `Mock(spec=JenkinsClient)` for builder tests to avoid real HTTP calls. Mock patching via `patch.object()` for jenkins client tests.
- **Packaging**: `entry_point.py` is the EXE entry (adds project root to path, calls `cli.main()`). `build.py` wraps PyInstaller with `--onefile`/`--onedir`, hidden imports for questionary/prompt_toolkit/wcwidth, and excludes unused modules (tkinter, matplotlib, numpy, pandas, PIL).

## Dependencies

- `requests` — Jenkins HTTP API
- `questionary` — interactive terminal UI (checkbox, select, confirm, text prompts)
- `pillow` — app icon processing (optional, for build only)
- `prompt_toolkit` — transitive via questionary; imported eagerly during interactive mode to pre-load on Windows
- dev: `pytest`, `pyinstaller`, `colorama` (Windows ANSI support, auto-detected/optional)

## Data persistence

- `data/build_history.json` — build records stored relative to config file parent directory. Auto-created on first use. Max 100 records.
- `jenkins_logs/` (configurable, default `./jenkins_logs`) — build logs organized as `build_YYYYMMDD/` subdirectories. Old logs auto-cleaned after `log_retention_days`.
