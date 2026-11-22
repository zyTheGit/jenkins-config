# AGENTS.md

This is the single rule set — no per-area rule files. CLI flags come from `cli.py`'s argparse; read it instead of recalling.

## Commands

```bash
uv run pytest tests/ -v                       # all; add --cov=jenkins_config --cov-report=term-missing
uv run pytest tests/test_config.py -v         # one file
uv run pytest tests/test_config.py::test_load_yaml -v
uv run pytest tests/test_mcp -v               # skips wholesale without the "mcp" extra

uv run python build.py                        # single-file CLI
uv run python build.py --dir                  # directory mode
uv run python build.py --target mcp           # --target all = CLI + MCP
uv run python build.py --clean
```

## Seams

One owner per seam; callers invoke it and never restate its rules.

- `paths.py` — config-file priority, address resolution, history anchoring.
- `build_flow.py` — job selection, parameter resolution, both trigger strategies.
- `config_load.py` — parsing, validation, legacy compatibility.
- `config_io.py` — serialization, saving, template specs.
- `config.py` — the `Config` class and derived jobs.
- `mcp/utils.py` — write gates, allowlist, config inspection.
- `mcp/errors.py` — failure payloads.
- CLI and MCP are adapters: confirmation, gates, rendering, payload wording.

## Config

- Every Jenkins job parameter lives in `params: dict`; nothing is hardcoded. A new plugin parameter is a config edit with zero code.
- `params` accepts a mapping or `"K=v&k2=v2"`; legacy JSON keys keep loading with a deprecation warning.
- Priority: CLI > project > environment > default. `Job.branch` is derived from `params[branch_field]`; `Environment.branch_field` overrides the global one.
- `Config` is one real class (`load`/`save`/`to_dict`/`get_jobs`) so `Mock(spec=Config)` works; `config_types.py` stays dataclasses-only.
- Serialize `server`/`build` with `dataclasses.asdict`. New fields must flow through save/template/`show_config`; the round-trip and template-coverage tests are the lock.
- One template source: `config_io.TEMPLATE_FIELD_SPECS` / `template_text()` feeds `--init`, `--help-config` and MCP `init_config`.
- Template credentials stay `PLACEHOLDER_VALUES`; never infer them from env or another config.

## Build flow

- `build_flow.py` implements both strategies: blocking (trigger → queue poll → status poll → log fetch → `BuildResult`) and queued (`trigger_queued`, used by MCP, records a `BUILDING` entry).
- Parallel execution is CLI-only (`run_blocking` + `ThreadPoolExecutor`).
- Failed trigger or queue timeout goes through `build_errors.save_error_log()`; `extract_error_lines()` picks the relevant console lines.
- History is `<config dir>/data/build_history.json`, newest `MAX_RECORDS=100`; a corrupt file is preserved as `.corrupt` before starting empty. Build logs live under `build.log_dir` and are pruned by `build.log_retention_days`.

## Interactive & CLI

- Interactive is a 5-step state machine (kind → env → projects → mode → confirm); a single project skips the mode step. ESC goes back one step; Ctrl+C / `q` / EOF abort.
- Import each `cmd_*` module inside its argparse branch. `cmd_interactive` imports `prompt_toolkit` at module top, so keep it lazy.
- Testing `cli.main()`: patch the source module (`cmd_list.*`), not `cli.*`. For interactive tests, patch `questionary.select/checkbox/confirm` and drive `.ask`.

## MCP server

- stdout is the JSON-RPC channel: logs and prints go to stderr or `JENKINS_MCP_LOG_FILE`.
- Writes require `JENKINS_MCP_ALLOW_WRITE=1`; direct `jenkins_url` is limited by `JENKINS_MCP_ALLOWED_HOSTS` once set.
- Inventory is a contract: 14 tools, 4 resources, 3 prompts (tool and prompt counts are asserted in `tests/test_mcp/test_server.py`).
- Boundaries: `show_config` = what is configured; `where_config` = where it came from (path only, never loads content; a denied path omits `exists`/`history_path` and merges `config_path_denied`); `health_check` = one network probe; `doctor` = local checks with zero network by default. No `config://location` resource.
- Logging: `setup_logging()` from `main()`; `JENKINS_MCP_LOG_LEVEL` (default WARNING), `JENKINS_MCP_LOG_FILE` (path or `auto`). Rotated and pruned per pid; a setup failure degrades to a stderr notice.

## Failure payloads

- Every failure carries exactly `error_code`, `error`, `config_path`, `next_steps`, `docs`. `next_steps` is never empty and every entry is executable. Never fabricate business data.
- `ErrorCode` is a closed set chosen by user action, not exception type. Allowlist rejection and filesystem permission are both `PermissionError` with opposite fixes, so `classify(exc, phase)` always receives the phase. Incomplete vs. syntax is decided by `config_load.VALIDATION_ERROR_PREFIX`; Jenkins connection errors bypass `classify()`.
- When the config is unusable, a read tool returns the failure payload — never an empty result that reads as "no data".
- `errors.py` builds every payload; `failure_text()` is the only wording joiner; `is_config_unusable()` is the only reader of that subset. `utils` may import `errors`, not vice versa.
- Placement by return type: dict tools merge fields at top level; list tools return a one-element list holding only the payload; trigger tools keep `triggered`/`failed` and append the fields.
- Every `ErrorCode.X` reference must resolve — enforced by the AST-scan test in `test_errors.py`.

## Config addressing & write safety

- Resolution order: explicit arg → `JENKINS_MCP_CONFIG` → `search_bases()`. The env var is honored only in `mcp/utils.resolve_config_path` and only with absolute paths. User dir is `~/.jenkins-config`; a missing home warns and skips. History goes through `history_path_for()` (allowlist first).
- Allowlist: a caller's `config_path` must resolve under `paths.search_bases()` or be exactly `JENKINS_MCP_CONFIG`; `JENKINS_MCP_CONFIG_ROOTS` extends it. Decide `path_allowed` via `utils._path_allowed`; don't re-implement `_is_within`.
- Too-broad bases (filesystem root or home itself) are not allowlist roots. A `<drive-root>/.jenkins-config` write → `config_path_denied`; `~/.jenkins-config` remains valid. The check lives in `utils.write_target_denied()` and applies to both `init_config` and `save_config`.
- `init_config` gate is graded: missing target + `overwrite=false` → create; existing + `overwrite=false` → `config_exists`, write nothing; `overwrite=true` → allow-write gate, then `.bak` → lock → atomic replace. `target` is only `user` or `cwd`.
- Shadowing follows `paths.config_rank()`: a write that would shadow an effective config → `config_exists` naming it. Success echoes `shadowed_path` and `effective_path`; when the written file is not effective, `next_steps[0]` says so.
- Write mechanics: validate `format` before writing; exists re-check, `.bak` and atomic replace happen in one `file_lock(required=True)`; reclaim a directory created only for this write on failure, except lock timeout → `unknown_error`. Config and history writes are always atomic replacements under a sibling `.lock`.
- `doctor` reports a fixed 11-check list. Unset `write_gate`/`allowed_hosts` is `warn` and never escalates; `runtime_mode` is always `ok`; a failed layer marks downstream `skip`; credentials show key names and 已配置/未配置 only; completeness is placeholder comparison.

## Testing

- Tests are hermetic: a tmp config, never the developer's `jenkins-config.yaml`; `tests/conftest.py` pins the user dir. Fake Jenkins with `Mock(spec=JenkinsClient)`; use `tmp_path` for files.
- Assert CLI output on stderr (`capsys.readouterr().err`).
- Always pass `encoding="utf-8"` to `read_text()` / `write_text()` — Windows defaults to GBK.
- `tests/test_mcp/` skips wholesale without the `mcp` extra; its conftest whitelists pytest's basetemp via `JENKINS_MCP_CONFIG_ROOTS`.
- New functionality ships with tests; test files mirror the package. Split by topic and keep new modules small — several existing modules are above 500 lines, so don't grow them.

## Style & dependencies

- 4-space indentation, snake_case, type hints on parameters and returns, Chinese docstrings with Args/Returns/Example.
- Runtime deps: `requests`, `questionary`, `pyyaml` only. `mcp` stays optional and imports lazily (`jenkins_config.mcp` must import without it).

## Release & docs

- Package entry points live in `pyproject.toml`; PyInstaller entry is `entry_point_mcp.py`. The npm launcher (`npm/`) resolves `JENKINS_MCP_BINARY` → `JENKINS_MCP_PYTHON` → cached binary → sha256-verified GitHub download → PATH; logs go to stderr. Keep the Windows `COMSPEC` / `taskkill` invariants (CVE-2024-27980, DEP0190) documented at the top of the launcher.
- Releases are tag-driven: `.github/workflows/build.yml` on `v*` builds binaries plus `checksums.txt` and publishes npm with the version from the tag. Never hand-edit the npm version; `NPM_TOKEN` is required; prereleases use the `next` tag.
- Behavior changes update `docs/`; new domain terms go in `GLOSSARY.md`. Client setup docs: `docs/mcp/README.md` §3. Issues via `gh`; labels live in `docs/agents/`.
