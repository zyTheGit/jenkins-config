# AGENTS.md

Rules for agents working in this repository. CLI flags are defined by `cli.py`'s argparse — read that file, never describe flags from memory. Domain vocabulary lives in `GLOSSARY.md`; deeper narratives in `docs/architecture.md`, `docs/cli.md`, `docs/mcp/README.md`.

## Build / Test Commands

```bash
# Run all tests
uv run pytest tests/ -v

# Run with coverage
uv run pytest tests/ --cov=jenkins_config --cov-report=term-missing

# Run a specific test file / single test
uv run pytest tests/test_config.py -v
uv run pytest tests/test_config.py::test_load_yaml -v

# Run MCP tests only (the whole dir auto-skips via pytest.importorskip when the "mcp" extra is missing)
uv run pytest tests/test_mcp -v

# Package to a single-file CLI binary (~14 MB)
uv run python build.py

# Directory mode (faster startup) / MCP binary / clean rebuild
uv run python build.py --dir
uv run python build.py --target mcp     # --target all for CLI + MCP
uv run python build.py --clean
```

## Architecture

```
cli.py (argparse + lazy dispatch per flag)
    ├── cmd_list.py · cmd_build.py · cmd_init.py · cmd_interactive.py
    │       ├── config.py        Config + business methods (types in config_types.py,
    │       │                    load/save/template in config_io.py)
    │       ├── build_flow.py    parse_params / select_jobs / run_blocking / trigger_queued
    │       ├── jenkins.py       JenkinsClient, BuildStatus
    │       ├── history.py       HistoryManager, BuildRecord
    │       └── paths.py         anchoring, shared with MCP
    └── utils.py                 logging

mcp/server.py → mcp/tools/*.py → mcp/utils.py (probe, gates, clients)
                                    └→ mcp/errors.py (classification → payload)
                                    └→ jenkins_config core (config / build_flow / history / paths)
```

`build_flow.py` is the single seam for job selection, parameter resolution and
both trigger strategies. CLI and MCP are adapters: they own confirmation,
write gates, log rendering and payload wording; the flow rules live here.

The other seams are named the same way, and each has exactly one owner — do not
re-implement its rules in a caller:

| Seam | Owner | What it alone decides |
|------|-------|-----------------------|
| Path anchoring & priority | `paths.py` | Which config file is in effect (`config_candidates()` / `config_rank()` / `resolve_config_file()`) and where the history file lives (`resolve_history_path()`) |
| Build orchestration | `build_flow.py` | Job selection, parameter resolution, blocking vs. queued triggering |
| Config shape | `config_types.py` + `config_io.py` + `config.py` | Dataclasses, load/save/template, business methods (patched onto `Config`) |
| MCP failure reporting | `mcp/errors.py` | Exception / inspection → the five-field actionable payload |
| MCP write gates | `mcp/utils.py` | `write_allowed()`, `write_target_denied()`, allowed config bases |

## MCP Server

### Packaging & release

- Entry point: `jenkins-config-mcp = jenkins_config.mcp.server:main`; PyInstaller entry is `entry_point_mcp.py`. Tool modules live in `mcp/tools/<topic>_tools.py` (config / build / history / diagnose / where / doctor / init); `utils.py` owns shared probes and write gates, `errors.py` owns failure payloads
- npx distribution: `npm/` publishes `@zythegit/jenkins-config-mcp` (`npx -y @zythegit/jenkins-config-mcp`); the launcher downloads the sha256-verified platform binary from GitHub Release on first run (cached under `~/.cache/jenkins-config-mcp/<tag>/`), so consumers need only Node 18+. Resolution order: `JENKINS_MCP_BINARY` → `JENKINS_MCP_PYTHON` → cached binary → download → PATH `jenkins-config-mcp`/`uvx`/`python`. All launcher logs go to stderr. On Windows it deliberately forwards `.cmd` / `.bat` shims through `COMSPEC` and delivers signals as `taskkill /T /F`; the spawn / escaping / signal invariants (CVE-2024-27980, DEP0190) are documented at the top of `npm/bin/jenkins-config-mcp.js` — keep them there and keep them intact
- Release assets are built by `.github/workflows/build.yml` on `v*` tags: `jenkins-config-mcp-{win-x64.exe,macos-x64,macos-arm64,linux-x64,linux-arm64}` + `checksums.txt`. The `publish-npm` job then publishes `npm/` to npmjs.com with the version derived from the tag (`v1.6.0` → `1.6.0`) — never hand-edit the `npm/package.json` version. Requires an `NPM_TOKEN` secret (npm Automation token); pre-release tags go to the `next` dist-tag

### Tool contracts & gates

- **Write gate** — write operations (`trigger_build` / `rebuild_last` / `save_config`) require `JENKINS_MCP_ALLOW_WRITE=1`; direct-mode `jenkins_url` is restricted by `JENKINS_MCP_ALLOWED_HOSTS` (authoritative once set)
- **Tool boundaries** — 14 tools / 4 resources / 3 prompts. Boundaries are deliberate and should not be blurred: `show_config` answers "what's *in* the config", `where_config` answers "where did this config *come from*" (path-only probe, never loads config content, so credentials can't leak by construction); `health_check` is a single network probe against Jenkins, `doctor` is a local health check with **zero network requests by default**. No `config://location` resource exists — its fields would fully duplicate `where_config`, and a Resource URI template can't carry `config_path`, so the two would drift
- **`where_config`** — returns `path_allowed`, and for a **denied** `config_path` it omits `exists` / `history_path` and merges the `config_path_denied` failure payload instead. Reporting `exists` for arbitrary paths would turn the diagnosis tool into a filesystem existence probe that bypasses the `resolve_config_path` allowlist — one process must not carry two disagreeing boundaries, because the day stdio becomes a remote transport nobody will re-audit this tool. `path_allowed` is decided by calling `resolve_config_path()` and catching its `PermissionError` (`utils._path_allowed`), never by re-implementing `_is_within`. An unexpected probe failure returns a `unknown_error` payload rather than a bare `{"error": ...}` — a return body without `error_code` / `next_steps` can't be classified mechanically

### Failure payloads

- **Failure payload** (`mcp/errors.py`) — every tool failure carries `error_code` / `error` / `config_path` / `next_steps` / `docs`. `ErrorCode` is a closed set (`config_not_found`, `config_parse_error`, `config_path_denied`, `config_permission_denied`, `config_incomplete`, `home_unavailable`, `config_exists`, `write_not_allowed`, `invalid_target`, `unknown_error`) and `next_steps` is never empty — `NEXT_STEPS` supplies per-code defaults, and every entry must be an executable action (call tool X / set env var Y / edit file Z). `classify(exc, phase)` needs the phase because allowlist rejection and filesystem permission failure both raise `PermissionError` but have opposite fixes; `config_io.VALIDATION_ERROR_PREFIX` is what separates "field not filled" from "syntax broken" (both `ValueError`). Jenkins connection errors deliberately bypass `classify()` (`requests` exceptions are `OSError` subclasses and would be mislabelled `config_permission_denied`)
- **`errors.py` owns the whole path from raw material to payload — no tool assembles those five fields itself**: `failure_payload(code, …)` when the code is already known, `config_failure_payload(config_path, exc, action)` for a bare `except` branch (it re-runs only the *addressing* step to pick the phase, since allowlist rejection and permission failure are both `PermissionError`, and leaves `config_path` empty when addressing itself failed — echoing an unapproved path into "which config is in effect" would make the payload contradict the allowlist), and `unusable_config_payload(inspect_config(…), action)` for read-before-data tools that must short-circuit on `CONFIG_UNUSABLE_CODES` (`history_tools` used to keep its own copy of that gate, which is exactly how "probe says A, payload says B" happens). `is_config_unusable()` is the only reader of that subset and `failure_text(action, reason)` the only wording joiner. Consequences: `utils` no longer builds payloads (its `failure_result()` only wraps a payload into the `triggered` / `failed` container), and `resources._safe_dump()` serializes the same five fields instead of a bespoke `{"error": …}`, so a client reading both tools and resources crosses one failure shape. Module-level dependency stays `utils → errors`; `config_failure_payload` needs `resolve_config_path`, so it imports it **inside the function** — no load-time cycle
- **ErrorCode references** — every `ErrorCode.X` reference in `jenkins_config/mcp/` is checked to resolve (`test_errors.py::test_every_error_code_reference_resolves`, AST scan). This is a regression test, not a type-checker substitute: a typo'd attribute sits in a branch that only concurrency/permission failures reach, so `init_tools` shipped `ErrorCode.UNKNOWN_ERROR` (→ `AttributeError` instead of a payload) until the lock-timeout branch was finally covered
- **Payload placement per return type** — dict tools merge the five fields at top level (`health_check` keeps `reachable` / `url`); list tools (`list_environments` / `list_projects` / `show_history`) return a **single-element list holding only the payload** — the old `{"name": "error", ...}` shape made models treat the error as an environment named "error" — and their annotations widened to `list[dict[str, Any]]`; `trigger_build` / `rebuild_last` keep the `triggered` / `failed` container and append the fields at top level via `utils.failure_result(..., payload=...)`

### Config addressing, diagnostics & write safety

- **`init_config` uses a graded write gate, not the blanket one**: target missing + `overwrite=false` → create directly, `JENKINS_MCP_ALLOW_WRITE` **not required** (the gate protects existing assets; forcing it turns zero-config setup from 1 step into 3 and kills the "fresh install → `list_environments`" path); target exists + `overwrite=false` → always `config_exists`, nothing written, regardless of the gate; `overwrite=true` → gate **required**, then `.bak` copy → `file_lock(required=True)` → `atomic_write`. `target` accepts only `user` (`~/.jenkins-config/jenkins-config.yaml`) / `cwd` (`<CWD>/.jenkins-config/jenkins-config.yaml` — the dot dir, so a project-level layout matches the user-level one and one `.gitignore` line covers it), never an arbitrary path
- **Write-target safety**: a too-broad CWD (drive root) is rejected as `config_path_denied` with two exits (`target='user'`, or `JENKINS_MCP_CONFIG_ROOTS`) — or as `home_unavailable` when HOME/USERPROFILE is missing as well, since "use `target='user'`" would then fail too — while CWD == home is allowed because the target then *is* `~/.jenkins-config`. That check lives in `utils.write_target_denied()` and is called by **both** `init_config` and `save_config`: it forwards to `utils._is_too_broad()` rather than re-implementing it, compares resolved paths on both sides (a symlinked home must not turn into a false rejection), and only applies to the `<host>/.jenkins-config` form — flat targets are already governed by the allowlist, and re-judging them would also block the deployer-trusted `JENKINS_MCP_CONFIG` file; a target that *is* exactly the `JENKINS_MCP_CONFIG` file is exempt even in dot-dir form, otherwise the read path trusts it while the write path rejects it and offers an exit ("move it to the user dir") that contradicts the deployer's setting. Its `next_steps` come from `utils.write_target_denied_steps()`, so both callers share one wording
- **Shadowing is judged by `paths.config_rank()`** — the position of a file in `paths.config_candidates()`, i.e. directory order × `CONFIG_FILE_NAMES` order, the same list `resolve_config_file()` probes, so "which config wins" has one source (`init_tools._shadow_relation()` ranks, it no longer re-sorts) — a directory-rank-only check misses a `.yml` / `.json` sitting next to the generated `.yaml`, which `resolve_config_file()` outranks — so `target='user'` (last candidate) is never blocked by a project-level config. A target that does *not* exist but whose creation would shadow an already-effective config (the dot dir outranks the flat file in the same directory, so a filled-in `jenkins-config.yaml` plus its `data/build_history.json` would silently stop being read) → `config_exists` with the shadowed path in `error`. A successful write echoes `shadowed_path` (empty string when nothing was shadowed) plus `effective_path`; the same rank comparison covers the **reverse** direction too: a file written below an already-effective config is created but not effective, so `effective_path` points at that other config and `next_steps[0]` says so instead of "edit this file → verify with `list_environments`"
- **Write mechanics**: Template credentials stay placeholders (`config_io.PLACEHOLDER_VALUES`) — never inferred from env vars or other config files. `format` is validated against `SUPPORTED_FORMATS` (currently `yaml` only) **before** anything is written and echoed back normalized, so `format='json'` can't drop JSON into `jenkins-config.yaml`; the exists re-check, `.bak` copy (`utils.backup_config_file()`, shared with `save_config`) and `atomic_write` all run inside **one** `file_lock` critical section, so a file created inside the TOCTOU window is reported as `config_exists` instead of being silently overwritten, and a directory created solely for this write is reclaimed on failure (`_discard_created_dir()`, lock sentinel included — sentinel name comes from `filelock.lock_path_for()`, never re-spelled locally) so "nothing was changed" doesn't leave an empty `.jenkins-config` behind; the **lock-timeout** path deliberately skips that reclamation and returns `unknown_error` ("another process is writing, retry later") rather than `config_permission_denied` — the sentinel then belongs to a live holder, and on POSIX deleting it would break mutual exclusion outright. A `.bak` that already exists falls back to `<name>.<timestamp>.bak` — a fixed name means the second overwrite replaces the only surviving copy of the credentials
- **`doctor`** — `doctor(config_path="", include_jenkins=False)` reports 11 fixed checks: `config_located`, `config_readable`, `config_parsable`, `config_complete`, `config_path_allowed`, `write_gate`, `allowed_hosts`, `history_path`, `log_sink`, `runtime_mode`, `jenkins_reachable`. Unset `write_gate` / `allowed_hosts` are only `warn` (read-only mode and "fall back to config `server.url`" are intended defaults, not faults) and never raise the overall status; `runtime_mode` is always `ok` and excluded from escalation (`INFO_ONLY_CHECKS`); a failing layer marks its downstream `skip` instead of reporting the same root cause three times; `history_path` probes read-only and never creates directories. Credentials appear as key name + 已配置/未配置 only. "Config complete" can only be judged by placeholder comparison — the template values pass `_validate_config`, so an un-filled config loads fine
- **Config allowlist** — a caller-supplied `config_path` must resolve inside `paths.search_bases()` (each of project root / CWD / exe dir plus their `.jenkins-config` subdir, and the user config dir), or be exactly the file `JENKINS_MCP_CONFIG` points at (deployer-set, therefore trusted — its parent dir is *not* whitelisted); extend the allowlist with `JENKINS_MCP_CONFIG_ROOTS` (os.pathsep-separated). Auto-probed bases additionally pass `utils._is_too_broad()`, which drops the filesystem root and the home dir itself — stdio CWD is uncontrollable and either one would make `_is_within` true for every path; `JENKINS_MCP_CONFIG_ROOTS` entries are deployer-set and skip that filter
- **Resolution order** — explicit arg → `JENKINS_MCP_CONFIG` → probe `search_bases()`. The env var is applied in `mcp/utils.resolve_config_path` only (absolute paths only; relative values are warned and ignored) so it never changes CLI probing. The user-level dir is `~/.jenkins-config` on all three platforms (`paths.user_config_dir()`; `user_log_dir()` = `~/.jenkins-config/logs`) — `platformdirs` was dropped deliberately: npx/EXE users have no project dir, so "where does the config go" must fit in one sentence, and per-OS dirs also made config-vs-data collapse on Windows/macOS while splitting on Linux. `Path.home()` raises `RuntimeError` without HOME/USERPROFILE, so `search_bases()` logs a warning and skips the user-level candidate instead of failing the whole probe. `build_history.json` is always `<config dir>/data/build_history.json` (`paths.resolve_history_path()` owns that anchoring rule and works from any config path, existing or not; MCP callers go through `mcp/utils.history_path_for()`, which applies the allowlist **first** — i.e. `~/.jenkins-config/data/` for npx), unrelated to the version-tagged npx cache, so upgrades can't drop history

### Logging & docs

- **Logging** — logs go to **stderr only** (stdout is the JSON-RPC channel). `mcp/server.py:setup_logging()` runs from `main()` and only recycles its own handlers; `JENKINS_MCP_LOG_LEVEL` (whitelist of DEBUG..CRITICAL, default `WARNING`) and `JENKINS_MCP_LOG_FILE` (a path, or `auto` → `~/.jenkins-config/logs/jenkins-config-mcp.<pid>.log`, 1 MB × 3 rotation, pruned to the newest `LOG_FILE_KEEP=5` pid files at startup) control it. The handlers are `_SafeStreamHandler` / `_SafeRotatingFileHandler` subclasses that swallow their own write errors, so the process-global `logging.raiseExceptions` stays untouched; log-path resolution sits inside the same `try` as handler creation (`~` expansion raises `RuntimeError` without HOME) and any failure prints the degradation notice straight to `sys.stderr`, since a `root.warning` would be filtered at `LOG_LEVEL=ERROR`
- **Client docs** — install docs live in `docs/mcp/README.md` §3: per-client registration (Claude Code `claude mcp add` + local/project/user scopes, Claude Desktop, Cursor, VS Code `.vscode/mcp.json` uses `servers` not `mcpServers`, Inspector), why shell-exported env vars never reach the server (stdio passes only a platform allowlist — must use the `env` key), config file placement for npx/EXE, a verification checklist, and §3.9 the zero-config path (`doctor` → `init_config` → user fills `server.url` / `server.token` → `list_environments`, mirrored by the `setup_workflow` prompt). §3.9 also warns that `where_config` / `doctor` echo absolute paths (usually containing the OS username) into the model context

## Code Style

- **Formatting**: 4-space indentation, snake_case functions
- **Type hints**: Use type annotations for function parameters and returns
- **Docstrings**: Use Chinese docstrings with Args/Returns/Example sections
- **Imports**: Standard library → Third-party → Local modules

## Key Patterns

1. **Job Key Format**: `env_project_name` (dashes → underscores)
2. **Parameter Priority**: CLI params > Project params > Environment params > Default
3. **Config path resolution**: an explicit arg wins, and `JENKINS_MCP_CONFIG` applies to the MCP Server only. The priority list is written **once**, in `paths.config_candidates()` (directory order × `CONFIG_FILE_NAMES`); `resolve_config_file()` probes it and `config_rank()` compares against it. History anchoring is written once in `paths.resolve_history_path()`; MCP callers reach it only through `mcp/utils.history_path_for()` (allowlist **first**, then anchoring) — deliberately not named `resolve_history_path`, so an unaddressed clone can't be imported by accident. Each base is probed as `<base>/.jenkins-config` first, then `<base>`; source mode is project root → CWD → user dir, EXE mode swaps the project root for the exe dir. Never re-implement either rule in a caller.
4. **Lazy command dispatch**: `cli.py` imports each `cmd_*` module inside its own branch (fast startup, no import cycle). When testing `cli.main()`, patch the **source module** (`jenkins_config.cmd_list.list_environments`), never `jenkins_config.cli.list_environments`.
5. **`Config` is assembled by monkey-patching**: dataclasses live in `config_types.py`, I/O in `config_io.py`, business methods in `config.py`, which attaches them at import time (`Config.load`, `Config.get_jobs`). Call sites stay `Config.load(path)` while each file stays readable. Consequence for tests: `Mock(spec=Config)` fails — use a plain `Mock()`.
6. **Dynamic params, no hardcoded fields**: everything travels in `params: dict`; there are no `branch` / `git_param` / `default_branch` fields. `Config.branch_field` (default `"branch"`) names the param key CLI `-b` overwrites, `Environment.branch_field` overrides it per environment, and `Job.branch` is a **derived** field filled from `params[branch_field]` during `get_jobs()`. Adding a Jenkins plugin parameter is a config edit with zero code. Legacy JSON keys still load (with a deprecation warning), and `params` accepts both a dict and a `"KEY=val&k2=v2"` string.
7. **Single-job pipeline** (`build_flow.py`): trigger → queue-item polling → status polling → log fetch → `BuildResult`. Parallel mode is `ThreadPoolExecutor` + `as_completed` behind `run_blocking()` (CLI); the MCP side calls `trigger_queued()` instead and persists a `BUILDING` placeholder record.
8. **Interactive mode** (`cmd_interactive.py`): a 5-step state machine (build kind → environment → projects → build mode → confirm), with step 4 skipped for a single project. ESC walks one step back: `_install_esc_back()` adds an Escape binding to the prompt_toolkit `Application` before every `.ask()` and returns the `_BACK` sentinel; Ctrl+C / `q` / EOF aborts.
9. **MCP I/O constraints**: `stdout` is the JSON-RPC channel, so *any* `print`/log must go to stderr or `JENKINS_MCP_LOG_FILE`; the server is read-only unless `JENKINS_MCP_ALLOW_WRITE=1`; a failing tool returns the actionable payload instead of fabricated business data.

## Testing

- **pytest + unittest.mock** — Jenkins is faked with `Mock(spec=JenkinsClient)`, file I/O uses `tmp_path`.
- **Never depend on the developer's machine** — a test brings its own tmp config; it must not read a real `jenkins-config.yaml`. `tests/test_mcp/conftest.py` whitelists pytest's basetemp through `JENKINS_MCP_CONFIG_ROOTS`, and `tests/conftest.py` pins `paths.user_config_dir()` via the shared `isolated_user_dir` / `patched_user_dir` fixtures.
- **questionary mocking** — patch the factories (`questionary.select`, `.checkbox`, `.confirm`) and drive `.ask.return_value` / `.ask.side_effect` for multi-step prompts.
- **Assertions on output** — headers and logs go to stderr, so read `capsys.readouterr().err`.
- **File encoding** — always pass `encoding="utf-8"` to `Path.write_text()` / `read_text()`; Windows otherwise defaults to GBK.
- **500-line ceiling** per source and test file — split by topic when exceeded (e.g. `test_cmd_build.py` vs `test_cmd_build_run.py`).
- **Layout** — test files mirror the package (`test_cmd_build*.py`, `test_config*.py`, `test_build_flow*.py`, `test_paths.py`, …); MCP tests live in `tests/test_mcp/test_<tool>_tools.py`.
- New functionality ships with tests. `tests/test_mcp/` skips as a whole when the `mcp` extra is absent (`pytest.importorskip`), so keep core-behaviour tests outside it.

## Error Handling & Data

- A failed trigger or queue timeout writes a structured `.log` via `build_errors.save_error_log()` (diagnostics + suggested checks); `extract_error_lines()` picks the relevant lines out of a console log by known keywords.
- `data/build_history.json` lives beside the config (`<config dir>/data/`), keeps the newest `MAX_RECORDS = 100` records, and is created on first use; an unparseable file is preserved as `build_history.json.corrupt` before continuing from empty history.
- `jenkins_logs/` (configurable via `build.log_dir`) stores logs under `build_YYYYMMDD/` subdirectories, pruned by `build.log_retention_days`.
- Config and history writes are atomic replacements guarded by a sibling `.lock`; `save_config` additionally leaves a `.bak` backup.

## Config Template

`--init`, `--help-config`, and MCP `init_config` must all render from `config_io.TEMPLATE_FIELD_SPECS` / `template_text()`. Never add a second template path — sample files (`jenkins-config.example.*`) do not exist in EXE / npx form.

## Dependencies

- Keep runtime dependencies to `requests`, `questionary`, `pyyaml`.
- `mcp` stays an optional extra and must import lazily (`jenkins_config.mcp` is importable without it).
- `prompt_toolkit` arrives via questionary; `cmd_interactive` imports it at module top, so the CLI must keep importing that module lazily.
- PyInstaller excludes PIL/tkinter/numpy; the icon comes from `assets/logo.ico` (`--icon` overrides).

## Docs & Agent Skills

- `docs/` holds the long-form references (architecture, CLI flags, configuration, usage/troubleshooting, MCP). Update them with behavior changes instead of duplicating here.
- `GLOSSARY.md` names domain terms; keep it current when a concept is coined.
- Issue tracker: GitHub Issues via `gh` (`docs/agents/issue-tracker.md`); triage labels `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix` (`docs/agents/triage-labels.md`); domain docs are root `CONTEXT.md` + `docs/adr/`, created lazily (`docs/agents/domain.md`).