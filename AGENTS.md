# AGENTS.md

Jenkins automation tool for triggering and monitoring builds. Refactored to pure Python with modular architecture.

## Build / Test Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_config.py -v

# Run the CLI directly
uv run python -m jenkins_config.cli --help

# Use shell wrapper
./jenkins-auto-build.sh --list-envs
./jenkins-auto-build.sh -i          # Interactive mode
./jenkins-auto-build.sh -e dev      # Build dev environment
```

## Package to EXE

```bash
# Install PyInstaller
uv pip install pyinstaller

# Build single exe file
uv run python build.py

# Build with directory mode (faster startup)
uv run python build.py --dir

# Clean and rebuild
uv run python build.py --clean

# Output: dist/jenkins-build.exe (~14 MB)
```

## Project Structure

```
jenkins-config/
├── jenkins-auto-build.sh       # Shell wrapper (calls Python CLI)
├── pyproject.toml              # Python project config
├── jenkins-config.json         # Configuration file
├── jenkins-config.example.json # Example config
├── build.py                    # PyInstaller build script
├── entry_point.py              # EXE entry point
├── jenkins_config/             # Python package
│   ├── __init__.py
│   ├── cli.py                  # CLI entry point
│   ├── config.py               # Config loading/parsing
│   ├── paths.py                # Config/history path anchoring (shared by CLI + MCP)
│   ├── filelock.py             # Cross-process file lock + atomic write
│   ├── jenkins.py              # Jenkins API client
│   ├── builder.py              # Build orchestration
│   ├── history.py              # Build history persistence
│   └── utils.py                # Logging utilities
├── tests/                      # Test suite
│   ├── test_mcp/               # MCP tests (auto-skipped when mcp extra missing)
│   ├── test_config.py
│   ├── test_jenkins.py
│   ├── test_builder.py
│   ├── test_history.py
│   └── test_utils.py
├── data/                       # Data directory
│   └── build_history.json      # Build history (generated)
└── dist/                       # Built executables (generated)
    └── jenkins-build.exe
```

MCP Server package layout (under `jenkins_config/`):

```
jenkins_config/mcp/             # MCP Server (optional, requires "mcp" extra)
├── server.py                   # Entry point (lazy FastMCP instance, stdio transport)
├── utils.py                    # Shared helpers (config path resolution, clients)
├── resources.py                # MCP Resources (read-only data endpoints)
├── prompts.py                  # MCP Prompts (workflow templates)
└── tools/                      # MCP Tools (11 tools)
    ├── config_tools.py         # list_environments, list_projects, show_config, save_config
    ├── build_tools.py          # trigger_build, rebuild_last
    ├── history_tools.py        # show_history, show_history_stats
    └── diagnose_tools.py       # health_check, get_build_status, get_build_log
```

## Architecture

```
cli.py (entry)
    ├── config.py (Config, Job, Environment)
    ├── jenkins.py (JenkinsClient, BuildStatus)
    ├── builder.py (Builder, BuildResult)
    ├── history.py (HistoryManager, BuildRecord)
    └── utils.py (logging functions)
```

## CLI Commands

```bash
# List environments
jenkins-build --list-envs

# List projects
jenkins-build --list-projects [ENV]

# Interactive build selection
jenkins-build -i

# Build specific environment
jenkins-build -e dev

# Build specific projects
jenkins-build -j dev:project-a,test:project-b

# View build history
jenkins-build --history

# View history statistics
jenkins-build --history-stats

# Use custom config file
jenkins-build -c /path/to/config.json --list-envs
```

## MCP Server

```bash
# Install MCP optional dependency (extra "mcp": mcp[cli]>=1.25.0,<2.0.0)
uv sync --extra mcp

# Run the MCP Server (stdio transport; console entry defined in pyproject.toml)
jenkins-config-mcp

# Or run the module directly (local development)
uv run python -m jenkins_config.mcp.server

# Run MCP tests (auto-skipped via pytest.importorskip when mcp is not installed)
uv run pytest tests/test_mcp -v

# Debug with MCP Inspector
uv run mcp dev jenkins_config/mcp/server.py

# Build the MCP Server binary (self-contained, no Python needed at runtime)
uv run python build.py --target mcp     # or --target all for CLI + MCP

# npx launcher self-check (prints the resolved command instead of starting)
JENKINS_MCP_LAUNCHER_DRYRUN=1 node npm/bin/jenkins-config-mcp.js
```

- Entry point: `jenkins-config-mcp = jenkins_config.mcp.server:main`; PyInstaller entry is `entry_point_mcp.py`
- npx distribution: `npm/` is a Node launcher package published as `@zythegit/jenkins-config-mcp` (`npx -y @zythegit/jenkins-config-mcp`) that downloads the platform binary from GitHub Release on first run (sha256-verified against `checksums.txt`, cached under `~/.cache/jenkins-config-mcp/<tag>/`), so consumers need only Node 18+. Resolution order: `JENKINS_MCP_BINARY` → `JENKINS_MCP_PYTHON` → cached binary → download → PATH `jenkins-config-mcp`/`uvx`/`python`. All launcher logs go to stderr; stdout is the JSON-RPC channel
- Release assets are built by `.github/workflows/build.yml` on `v*` tags: `jenkins-config-mcp-{win-x64.exe,macos-x64,macos-arm64,linux-x64,linux-arm64}` + `checksums.txt`. The `publish-npm` job then publishes `npm/` to npmjs.com with the version derived from the tag (`v1.6.0` → `1.6.0`) — never hand-edit the `npm/package.json` version. Requires an `NPM_TOKEN` secret (npm Automation token); pre-release tags go to the `next` dist-tag
- Write operations (`trigger_build` / `rebuild_last` / `save_config`) require `JENKINS_MCP_ALLOW_WRITE=1`; direct-mode `jenkins_url` is restricted by `JENKINS_MCP_ALLOWED_HOSTS` (authoritative once set)
- Caller-supplied `config_path` must resolve inside `paths.search_bases()` (project root / CWD / exe dir / user config dir), or be exactly the file `JENKINS_MCP_CONFIG` points at (deployer-set, therefore trusted — its parent dir is *not* whitelisted); extend the allowlist with `JENKINS_MCP_CONFIG_ROOTS` (os.pathsep-separated). Auto-probed bases additionally pass `utils._is_too_broad()`, which drops the filesystem root and the home dir itself — stdio CWD is uncontrollable and either one would make `_is_within` true for every path; `JENKINS_MCP_CONFIG_ROOTS` entries are deployer-set and skip that filter
- Config resolution order: explicit arg → `JENKINS_MCP_CONFIG` → probe `search_bases()`. The env var is applied in `mcp/utils.resolve_config_path` only (absolute paths only; relative values are warned and ignored) so it never changes CLI probing. User-level dirs come from `platformdirs` (`paths.user_config_dir()` / `user_data_dir()` / `user_log_dir()`); when the config lives in the user config dir, `build_history.json` moves to the user data dir so npx version-tag cache churn can't drop history — unless `<config dir>/data/build_history.json` already exists, in which case the legacy path wins so upgrades don't silently blank existing history. Note both dirs are `%LOCALAPPDATA%\jenkins-config` on Windows
- Logs go to **stderr only** (stdout is the JSON-RPC channel). `mcp/server.py:setup_logging()` runs from `main()` and only recycles its own handlers; `JENKINS_MCP_LOG_LEVEL` (whitelist of DEBUG..CRITICAL, default `WARNING`) and `JENKINS_MCP_LOG_FILE` (a path, or `auto` → `user_log_dir()/jenkins-config-mcp.<pid>.log`, 1 MB × 3 rotation, pruned to the newest `LOG_FILE_KEEP=5` pid files at startup) control it. The handlers are `_SafeStreamHandler` / `_SafeRotatingFileHandler` subclasses that swallow their own write errors, so the process-global `logging.raiseExceptions` stays untouched; log-path resolution sits inside the same `try` as handler creation (`~` expansion raises `RuntimeError` without HOME) and any failure prints the degradation notice straight to `sys.stderr`, since a `root.warning` would be filtered at `LOG_LEVEL=ERROR`


- Client install docs live in `docs/mcp/README.md` §3: per-client registration (Claude Code `claude mcp add` + local/project/user scopes, Claude Desktop, Cursor, VS Code `.vscode/mcp.json` uses `servers` not `mcpServers`, Inspector), why shell-exported env vars never reach the server (stdio passes only a platform allowlist — must use the `env` key), config file placement for npx/EXE, and a verification checklist
- Full documentation: `docs/mcp/README.md` (11 tools, 4 resources, 2 prompts, config path resolution, write gate + host allowlist)

## Code Style

### Python

- **Formatting**: 4-space indentation, snake_case functions
- **Type hints**: Use type annotations for function parameters and returns
- **Docstrings**: Use Chinese docstrings with Args/Returns/Example sections
- **Imports**: Standard library → Third-party → Local modules

### Key Patterns

1. **Job Key Format**: `env_project_name` (dashes → underscores)
2. **Parameter Priority**: CLI params > Project params > Environment params > Default
3. **Config Path Resolution**: 
   - Explicit arg wins; `JENKINS_MCP_CONFIG` applies to the MCP Server only
   - Source mode: project root → CWD → user config dir
   - EXE mode: current working directory → exe directory → user config dir