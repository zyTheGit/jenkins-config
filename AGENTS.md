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
│   ├── test_paths.py           # search_bases_detail() / probe_report() ordering
│   └── test_utils.py
├── data/                       # Data directory
│   └── build_history.json      # Build history (generated)
└── dist/                       # Built executables (generated)
    └── jenkins-build.exe
```

MCP Server package layout (under `jenkins_config/`):

```
jenkins_config/mcp/             # MCP Server (optional, requires "mcp" extra)
├── server.py                   # Entry point (lazy FastMCP instance, stdio transport, current_log_sinks())
├── utils.py                    # Shared helpers (config path resolution, probe/inspect, clients)
├── errors.py                   # ErrorCode closed set, NEXT_STEPS, failure_payload(), classify()
├── resources.py                # MCP Resources (read-only data endpoints)
├── prompts.py                  # MCP Prompts (workflow templates)
└── tools/                      # MCP Tools (14 tools)
    ├── config_tools.py         # list_environments, list_projects, show_config, save_config
    ├── build_tools.py          # trigger_build, rebuild_last
    ├── history_tools.py        # show_history, show_history_stats
    ├── diagnose_tools.py       # health_check, get_build_status, get_build_log
    ├── where_tools.py          # where_config (config anchoring diagnosis, path-only)
    ├── doctor_tools.py         # doctor (local health check, 11 items, no network by default)
    └── init_tools.py           # init_config (config template bootstrap, graded write gate)
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
- npx distribution: `npm/` is a Node launcher package published as `@zythegit/jenkins-config-mcp` (`npx -y @zythegit/jenkins-config-mcp`) that downloads the platform binary from GitHub Release on first run (sha256-verified against `checksums.txt`, cached under `~/.cache/jenkins-config-mcp/<tag>/`), so consumers need only Node 18+. Resolution order: `JENKINS_MCP_BINARY` → `JENKINS_MCP_PYTHON` → cached binary → download → PATH `jenkins-config-mcp`/`uvx`/`python`. All launcher logs go to stderr; stdout is the JSON-RPC channel. A resolved command ending in `.cmd` / `.bat` (the Windows pip/npm console-script shim, i.e. exactly what the fallback branch hits) is forwarded through `COMSPEC` (default `cmd.exe`) with `/d /s /c` and cross-spawn-style escaping instead of being spawned directly — Node ≥18.20 / 20.12 throws `EINVAL` on batch files under `shell: false` (CVE-2024-27980), while `shell: true` is avoided because Node 24 deprecates passing args that way (DEP0190) and concatenates them unescaped; **argument** meta chars are escaped twice (a shim's `%*` gets re-parsed) while the command path is escaped once. Everything else keeps `shell: false`, and the dry-run output reports the decision as `via_cmd`
- Release assets are built by `.github/workflows/build.yml` on `v*` tags: `jenkins-config-mcp-{win-x64.exe,macos-x64,macos-arm64,linux-x64,linux-arm64}` + `checksums.txt`. The `publish-npm` job then publishes `npm/` to npmjs.com with the version derived from the tag (`v1.6.0` → `1.6.0`) — never hand-edit the `npm/package.json` version. Requires an `NPM_TOKEN` secret (npm Automation token); pre-release tags go to the `next` dist-tag
- Write operations (`trigger_build` / `rebuild_last` / `save_config`) require `JENKINS_MCP_ALLOW_WRITE=1`; direct-mode `jenkins_url` is restricted by `JENKINS_MCP_ALLOWED_HOSTS` (authoritative once set)
- 14 tools / 4 resources / 3 prompts. Tool boundaries are deliberate and should not be blurred: `show_config` answers "what's *in* the config", `where_config` answers "where did this config *come from*" (path-only probe, never loads config content, so credentials can't leak by construction); `health_check` is a single network probe against Jenkins, `doctor` is a local health check with **zero network requests by default**. No `config://location` resource exists — its fields would fully duplicate `where_config`, and a Resource URI template can't carry `config_path`, so the two would drift
- `where_config` returns `path_allowed`, and for a **denied** `config_path` it omits `exists` / `history_path` and merges the `config_path_denied` failure payload instead. Reporting `exists` for arbitrary paths would turn the diagnosis tool into a filesystem existence probe that bypasses the `resolve_config_path` allowlist — one process must not carry two disagreeing boundaries, because the day stdio becomes a remote transport nobody will re-audit this tool. `path_allowed` is decided by calling `resolve_config_path()` and catching its `PermissionError` (`utils._path_allowed`), never by re-implementing `_is_within`. An unexpected probe failure returns a `unknown_error` payload rather than a bare `{"error": ...}` — a return body without `error_code` / `next_steps` can't be classified mechanically
- Actionable failure payload (`mcp/errors.py`): every tool failure carries `error_code` / `error` / `config_path` / `next_steps` / `docs`. `ErrorCode` is a closed set (`config_not_found`, `config_parse_error`, `config_path_denied`, `config_permission_denied`, `config_incomplete`, `home_unavailable`, `config_exists`, `write_not_allowed`, `invalid_target`, `unknown_error`) and `next_steps` is never empty — `NEXT_STEPS` supplies per-code defaults, and every entry must be an executable action (call tool X / set env var Y / edit file Z). `classify(exc, phase)` needs the phase because allowlist rejection and filesystem permission failure both raise `PermissionError` but have opposite fixes; `config_io.VALIDATION_ERROR_PREFIX` is what separates "field not filled" from "syntax broken" (both `ValueError`). Jenkins connection errors deliberately bypass `classify()` (`requests` exceptions are `OSError` subclasses and would be mislabelled `config_permission_denied`)
- Payload placement per return type: dict tools merge the five fields at top level (`health_check` keeps `reachable` / `url`); list tools (`list_environments` / `list_projects` / `show_history`) return a **single-element list holding only the payload** — the old `{"name": "error", ...}` shape made models treat the error as an environment named "error" — and their annotations widened to `list[dict[str, Any]]`; `trigger_build` / `rebuild_last` keep the `triggered` / `failed` container and append the fields at top level via `utils.failure_result(..., payload=...)`
- `init_config` uses a **graded** write gate, not the blanket one: target missing + `overwrite=false` → create directly, `JENKINS_MCP_ALLOW_WRITE` **not required** (the gate protects existing assets; forcing it turns zero-config setup from 1 step into 3 and kills the "fresh install → `list_environments`" path); target exists + `overwrite=false` → always `config_exists`, nothing written, regardless of the gate; a target that does *not* exist but whose creation would **shadow** an already-effective config (the dot dir outranks the flat file in the same directory, so a filled-in `jenkins-config.yaml` plus its `data/build_history.json` would silently stop being read) → also `config_exists` with the shadowed path in `error`; shadowing is judged by comparing `search_bases()` rank **plus** the `CONFIG_FILE_NAMES` rank inside the same directory (`init_tools._shadow_relation()`) — dir rank alone misses a `.yml` / `.json` sitting next to the generated `.yaml`, which `resolve_config_file()` outranks — so `target='user'` (last candidate) is never blocked by a project-level config, and a successful write echoes `shadowed_path` (empty string when nothing was shadowed) plus `effective_path`; the same rank comparison covers the **reverse** direction too: a file written below an already-effective config is created but not effective, so `effective_path` points at that other config and `next_steps[0]` says so instead of "edit this file → verify with `list_environments`"; `overwrite=true` → gate **required**, then `.bak` copy → `file_lock(required=True)` → `atomic_write`. `target` accepts only `user` (`~/.jenkins-config/jenkins-config.yaml`) / `cwd` (`<CWD>/.jenkins-config/jenkins-config.yaml` — the dot dir, so a project-level layout matches the user-level one and one `.gitignore` line covers it), never an arbitrary path; a too-broad CWD (drive root) is rejected as `config_path_denied` with two exits (`target='user'`, or `JENKINS_MCP_CONFIG_ROOTS`) — or as `home_unavailable` when HOME/USERPROFILE is missing as well, since "use `target='user'`" would then fail too — while CWD == home is allowed because the target then *is* `~/.jenkins-config`. That check lives in `utils.write_target_denied()` and is called by **both** `init_config` and `save_config`: it forwards to `utils._is_too_broad()` rather than re-implementing it, compares resolved paths on both sides (a symlinked home must not turn into a false rejection), and only applies to the `<host>/.jenkins-config` form — flat targets are already governed by the allowlist, and re-judging them would also block the deployer-trusted `JENKINS_MCP_CONFIG` file; a target that *is* exactly the `JENKINS_MCP_CONFIG` file is exempt even in dot-dir form, otherwise the read path trusts it while the write path rejects it and offers an exit ("move it to the user dir") that contradicts the deployer's setting. Its `next_steps` come from `utils.write_target_denied_steps()`, so both callers share one wording. Template credentials stay placeholders (`config_io.PLACEHOLDER_VALUES`) — never inferred from env vars or other config files. `format` is validated against `SUPPORTED_FORMATS` (currently `yaml` only) **before** anything is written and echoed back normalized, so `format='json'` can't drop JSON into `jenkins-config.yaml`; the exists re-check, `.bak` copy (`utils.backup_config_file()`, shared with `save_config`) and `atomic_write` all run inside **one** `file_lock` critical section, so a file created inside the TOCTOU window is reported as `config_exists` instead of being silently overwritten, and a directory created solely for this write is reclaimed on failure (`_discard_created_dir()`, lock sentinel included — sentinel name comes from `filelock.lock_path_for()`, never re-spelled locally) so "nothing was changed" doesn't leave an empty `.jenkins-config` behind; the **lock-timeout** path deliberately skips that reclamation and returns `unknown_error` ("another process is writing, retry later") rather than `config_permission_denied` — the sentinel then belongs to a live holder, and on POSIX deleting it would break mutual exclusion outright. A `.bak` that already exists falls back to `<name>.<timestamp>.bak` — a fixed name means the second overwrite replaces the only surviving copy of the credentials
- `doctor(config_path="", include_jenkins=False)` reports 11 fixed checks: `config_located`, `config_readable`, `config_parsable`, `config_complete`, `config_path_allowed`, `write_gate`, `allowed_hosts`, `history_path`, `log_sink`, `runtime_mode`, `jenkins_reachable`. Unset `write_gate` / `allowed_hosts` are only `warn` (read-only mode and "fall back to config `server.url`" are intended defaults, not faults) and never raise the overall status; `runtime_mode` is always `ok` and excluded from escalation (`INFO_ONLY_CHECKS`); a failing layer marks its downstream `skip` instead of reporting the same root cause three times; `history_path` probes read-only and never creates directories. Credentials appear as key name + 已配置/未配置 only. "Config complete" can only be judged by placeholder comparison — the template values pass `_validate_config`, so an un-filled config loads fine
- Supporting API added for the above: `paths.search_bases_detail()` / `paths.probe_report()` / `paths.runtime_mode()` (`search_bases()` now filters the detail list, external behaviour unchanged; `runtime_mode()` is the **only** place that reads `sys.frozen` — `search_bases_detail()` calls it too, so `probe_report()['mode']` and doctor's `runtime_mode` check can't disagree); `mcp/utils.is_base_too_broad()` / `write_target_denied()` (shared write-target policy) / `probe_report_for_mcp()` / `ConfigInspection` / `inspect_config()` / `config_failure_payload()` / `backup_config_file()`; `mcp/server.current_log_sinks()` (read-only); `config_io.PLACEHOLDER_VALUES` / `VALIDATION_ERROR_PREFIX` / `TEMPLATE_HEADER` / `TEMPLATE_FIELD_SPECS` / `template_fields()` / `template_text()` — CLI `show_template()`, CLI `--init` silent mode (`cmd_init.run_init`) and MCP `init_config` all render the same specs, and the old "copy `jenkins-config.example.yaml/json` if present" branch was dropped because those files don't exist in EXE / npx form, so the same command produced two different initial configs
- New tests: `tests/test_paths.py`, `tests/test_mcp/test_where_tools.py`, `test_doctor_tools.py`, `test_errors.py`, `test_init_tools.py`; `tests/conftest.py` holds the shared `isolated_user_dir` / `patched_user_dir` fixtures (pointing `paths.user_config_dir()` at a temp dir — without it, assertions about fallback paths drift with whatever the dev machine has in `~/.jenkins-config`)
- Caller-supplied `config_path` must resolve inside `paths.search_bases()` (each of project root / CWD / exe dir plus their `.jenkins-config` subdir, and the user config dir), or be exactly the file `JENKINS_MCP_CONFIG` points at (deployer-set, therefore trusted — its parent dir is *not* whitelisted); extend the allowlist with `JENKINS_MCP_CONFIG_ROOTS` (os.pathsep-separated). Auto-probed bases additionally pass `utils._is_too_broad()`, which drops the filesystem root and the home dir itself — stdio CWD is uncontrollable and either one would make `_is_within` true for every path; `JENKINS_MCP_CONFIG_ROOTS` entries are deployer-set and skip that filter
- Config resolution order: explicit arg → `JENKINS_MCP_CONFIG` → probe `search_bases()`. The env var is applied in `mcp/utils.resolve_config_path` only (absolute paths only; relative values are warned and ignored) so it never changes CLI probing. The user-level dir is `~/.jenkins-config` on all three platforms (`paths.user_config_dir()`; `user_log_dir()` = `~/.jenkins-config/logs`) — `platformdirs` was dropped deliberately: npx/EXE users have no project dir, so "where does the config go" must fit in one sentence, and per-OS dirs also made config-vs-data collapse on Windows/macOS while splitting on Linux. `Path.home()` raises `RuntimeError` without HOME/USERPROFILE, so `search_bases()` logs a warning and skips the user-level candidate instead of failing the whole probe. `build_history.json` is always `<config dir>/data/build_history.json`, i.e. `~/.jenkins-config/data/` for npx — unrelated to the version-tagged npx cache, so upgrades can't drop history
- Logs go to **stderr only** (stdout is the JSON-RPC channel). `mcp/server.py:setup_logging()` runs from `main()` and only recycles its own handlers; `JENKINS_MCP_LOG_LEVEL` (whitelist of DEBUG..CRITICAL, default `WARNING`) and `JENKINS_MCP_LOG_FILE` (a path, or `auto` → `~/.jenkins-config/logs/jenkins-config-mcp.<pid>.log`, 1 MB × 3 rotation, pruned to the newest `LOG_FILE_KEEP=5` pid files at startup) control it. The handlers are `_SafeStreamHandler` / `_SafeRotatingFileHandler` subclasses that swallow their own write errors, so the process-global `logging.raiseExceptions` stays untouched; log-path resolution sits inside the same `try` as handler creation (`~` expansion raises `RuntimeError` without HOME) and any failure prints the degradation notice straight to `sys.stderr`, since a `root.warning` would be filtered at `LOG_LEVEL=ERROR`


- Client install docs live in `docs/mcp/README.md` §3: per-client registration (Claude Code `claude mcp add` + local/project/user scopes, Claude Desktop, Cursor, VS Code `.vscode/mcp.json` uses `servers` not `mcpServers`, Inspector), why shell-exported env vars never reach the server (stdio passes only a platform allowlist — must use the `env` key), config file placement for npx/EXE, a verification checklist, and §3.9 the zero-config path (`doctor` → `init_config` → user fills `server.url` / `server.token` → `list_environments`, mirrored by the `setup_workflow` prompt). §3.9 also warns that `where_config` / `doctor` echo absolute paths (usually containing the OS username) into the model context
- Full documentation: `docs/mcp/README.md` (14 tools, 4 resources, 3 prompts, config path resolution, write gate + host allowlist, §7.5 error codes and their next steps)

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
   - Each base is probed as `<base>/.jenkins-config` first, then `<base>` itself (`paths._with_app_dirs()`), so a project-level layout is structurally identical to `~/.jenkins-config/`
   - Source mode: project root → CWD → user config dir
   - EXE mode: current working directory → exe directory → user config dir