# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
cli.py (entry) ─┬── config.py    (Config, Job, Environment dataclasses; JSON config loader)
                ├── jenkins.py   (JenkinsClient, BuildStatus; HTTP API wrapper via requests)
                ├── builder.py   (Builder, BuildResult; parallel/sequential orchestration)
                ├── history.py   (HistoryManager, BuildRecord; JSON file persistence)
                └── utils.py     (ANSI-colored logging, debug mode, formatting)
```

- **cli.py**: Single entry point. Parses argparse args, dispatches to interactive mode (`-i`), list commands, history commands, or `run_build()`.
- **config.py**: `Config.load()` reads JSON config file. `get_jobs()` filters by env/project and merges params (project > env > default). `Job` key format: `{env}_{project_name}` (dashes → underscores).
- **jenkins.py**: `JenkinsClient` handles crumb-based CSRF, `trigger_build()` (POST), `get_build_number()` (queue polling), `get_build_status()` (polling), `get_build_log()`.
- **builder.py**: `Builder` runs jobs via `_build_single()` (trigger → poll for number → wait for completion → save log). `build_parallel()` uses `ThreadPoolExecutor`; `build_sequential()` is a simple loop.
- **history.py**: `HistoryManager` persists build records to `data/build_history.json`. Supports `get_last_build_group()` for rebuild-last feature. Max 100 records.
- **utils.py**: Logging to stderr with ANSI colors. Debug mode guarded by global flag.

## Key patterns

- **Config path resolution**: source mode uses `__file__` relative; EXE mode tries cwd first, then exe directory (checked via `sys.frozen`).
- **Param merge priority**: CLI `-p` > project params > env params > defaults.
- **Build flow**: trigger → queue → build number → poll status → save log → return `BuildResult`.
- **Interactive mode**: Uses `questionary` for multi-select, environment/project filtering, and branch choice.
- **Error log fallback**: When build fails to trigger or queue times out, a structured `.log` file with diagnostics is saved.

## Dependencies

- `requests` — Jenkins HTTP API
- `questionary` — interactive terminal UI
- `colorama` — Windows ANSI color support (optional, auto-detected)
- dev: `pytest`, `pyinstaller`, `colorama`
