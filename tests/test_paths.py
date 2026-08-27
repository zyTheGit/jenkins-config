# tests/test_paths.py
"""
路径锚定明细与探测报告测试

测试覆盖：
- search_bases_detail 的 order 严格递增与 kind 顺序
- .jenkins-config 子目录候选排在对应目录之前
- HOME 缺失时用户级候选降级为 skipped_reason='home_unavailable'
- search_bases 改为基于明细实现后行为不变（源码 / 冻结两种模式）
- probe_report 的 explicit_arg / probed / fallback 三态与 matched_file、history_path

不依赖 mcp extra：paths 模块本身对 mcp 无依赖，因此放在 tests/ 根目录。
"""

from pathlib import Path

from jenkins_config import paths


def _fake_project_root(tmp_path: Path, monkeypatch) -> Path:
    """伪造源码模式下的项目根目录

    paths.project_root() 由本模块文件位置上溯一级得到，
    因此改写 paths.__file__ 即可把项目根指到临时目录（与既有测试一致）。

    Args:
        tmp_path: pytest 临时目录
        monkeypatch: pytest monkeypatch fixture

    Returns:
        伪造的项目根目录
    """
    root = tmp_path / "repo"
    (root / "jenkins_config").mkdir(parents=True)
    monkeypatch.setattr(paths, "__file__", str(root / "jenkins_config" / "paths.py"))
    return root


def _isolate_bases(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """把三个候选目录全部指向临时目录，隔离真实仓库与用户家目录

    Args:
        tmp_path: pytest 临时目录
        monkeypatch: pytest monkeypatch fixture

    Returns:
        (项目根, CWD, 用户级配置目录) 三元组
    """
    root = _fake_project_root(tmp_path, monkeypatch)

    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    user_dir = tmp_path / "userconfig"
    user_dir.mkdir()
    monkeypatch.setattr(paths, "user_config_dir", lambda: user_dir)
    return root, cwd, user_dir


# ============================================================================
# search_bases_detail 测试
# ============================================================================


def test_search_bases_detail_order_is_strictly_increasing_from_one(tmp_path, monkeypatch):
    """验证 order 从 1 开始严格递增（客户端据此展示优先级）"""
    _isolate_bases(tmp_path, monkeypatch)

    details = paths.search_bases_detail()

    assert [item["order"] for item in details] == list(range(1, len(details) + 1))


def test_search_bases_detail_source_mode_kinds(tmp_path, monkeypatch):
    """验证源码模式候选顺序：每个目录先看 .jenkins-config 子目录，再看目录本身"""
    root, cwd, user_dir = _isolate_bases(tmp_path, monkeypatch)

    details = paths.search_bases_detail()

    assert [item["kind"] for item in details] == [
        "project_root_app_dir",
        "project_root",
        "cwd_app_dir",
        "cwd",
        "user_config_dir",
    ]
    assert [item["base"] for item in details] == [
        str(root / paths.APP_DIR_NAME),
        str(root),
        str(cwd / paths.APP_DIR_NAME),
        str(cwd),
        str(user_dir),
    ]
    assert all(item["skipped_reason"] == "" for item in details)


def test_search_bases_detail_frozen_mode_kinds(tmp_path, monkeypatch):
    """验证冻结（EXE）模式的候选类型顺序：CWD → exe 目录 → 用户级配置目录"""
    import sys

    _isolate_bases(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    details = paths.search_bases_detail()

    assert [item["kind"] for item in details] == [
        "cwd_app_dir",
        "cwd",
        "exe_dir_app_dir",
        "exe_dir",
        "user_config_dir",
    ]


def test_search_bases_detail_reports_matched_file(tmp_path, monkeypatch):
    """验证命中的配置文件按 CONFIG_FILE_NAMES 优先级记录到 matched_file"""
    root, _, _ = _isolate_bases(tmp_path, monkeypatch)
    (root / "jenkins-config.yaml").write_text("server: {}", encoding="utf-8")

    details = paths.search_bases_detail()

    # 项目根的 .jenkins-config 子目录不存在，命中落在下一位（项目根本身）
    assert details[0]["kind"] == "project_root_app_dir"
    assert details[0]["exists"] is False
    assert details[0]["matched_file"] == ""
    assert details[1]["matched_file"] == str(root / "jenkins-config.yaml")
    assert details[1]["exists"] is True


def test_app_dir_config_wins_over_flat_file(tmp_path, monkeypatch):
    """验证 <项目根>/.jenkins-config 里的配置优先于项目根顶层那份"""
    root, _, _ = _isolate_bases(tmp_path, monkeypatch)
    flat = root / "jenkins-config.yaml"
    flat.write_text("server: {}", encoding="utf-8")
    nested = root / paths.APP_DIR_NAME / "jenkins-config.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("server: {}", encoding="utf-8")

    assert paths.resolve_config_file() == nested


def test_search_bases_detail_marks_home_unavailable(tmp_path, monkeypatch):
    """验证 HOME 缺失时用户级候选不崩溃，base 为 None 并标注原因"""
    _fake_project_root(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    def _no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(_no_home))

    details = paths.search_bases_detail()
    last = details[-1]

    assert last["kind"] == "user_config_dir"
    assert last["base"] is None
    assert last["exists"] is False
    assert last["matched_file"] == ""
    assert last["skipped_reason"] == "home_unavailable"


# ============================================================================
# search_bases 行为一致性测试（改为基于明细过滤实现）
# ============================================================================


def test_search_bases_matches_detail_bases(tmp_path, monkeypatch):
    """验证 search_bases 与明细一致：应用子目录排在对应目录之前"""
    root, cwd, user_dir = _isolate_bases(tmp_path, monkeypatch)

    assert paths.search_bases() == [
        root / paths.APP_DIR_NAME,
        root,
        cwd / paths.APP_DIR_NAME,
        cwd,
        user_dir,
    ]


def test_search_bases_frozen_mode_unchanged(tmp_path, monkeypatch):
    """验证冻结模式下候选仍以 CWD → exe 目录 → 用户配置目录为骨架"""
    import sys

    _, cwd, user_dir = _isolate_bases(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe_dir = Path(sys.executable).resolve().parent

    assert paths.search_bases() == [
        cwd / paths.APP_DIR_NAME,
        cwd,
        exe_dir / paths.APP_DIR_NAME,
        exe_dir,
        user_dir,
    ]


def test_search_bases_skips_user_dir_without_home(tmp_path, monkeypatch):
    """验证 HOME 缺失时 search_bases 跳过用户级候选（旧行为保持不变）"""
    root = _fake_project_root(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)

    def _no_home() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(_no_home))

    assert paths.search_bases() == [
        root / paths.APP_DIR_NAME,
        root,
        tmp_path / paths.APP_DIR_NAME,
        tmp_path,
    ]


# ============================================================================
# probe_report 测试
# ============================================================================


def test_probe_report_explicit_arg(tmp_path, monkeypatch):
    """验证显式路径为 explicit_arg，且历史文件锚定在其同级 data/ 下"""
    _isolate_bases(tmp_path, monkeypatch)
    target = tmp_path / "explicit" / "jenkins-config.yaml"
    target.parent.mkdir()
    target.write_text("server: {}", encoding="utf-8")

    report = paths.probe_report(target)

    assert report["source"] == "explicit_arg"
    assert report["config_path"] == str(target)
    assert report["exists"] is True
    assert report["history_path"] == str(
        target.parent / "data" / "build_history.json"
    )
    assert report["mode"] == "source"
    assert report["candidate_file_names"] == list(paths.CONFIG_FILE_NAMES)


def test_probe_report_probed_hits_first_base(tmp_path, monkeypatch):
    """验证自动探测命中时 source 为 probed，且命中项记录在对应候选的 matched_file"""
    root, cwd, _ = _isolate_bases(tmp_path, monkeypatch)
    expected = root / "jenkins-config.yaml"
    expected.write_text("server: {}", encoding="utf-8")
    # CWD 里也放一份，验证优先级仍是项目根优先
    (cwd / "jenkins-config.yaml").write_text("server: {}", encoding="utf-8")

    report = paths.probe_report()

    assert report["source"] == "probed"
    assert report["config_path"] == str(expected)
    assert report["exists"] is True
    assert report["bases"][1]["matched_file"] == str(expected)
    assert report["history_path"] == str(root / "data" / "build_history.json")


def test_probe_report_fallback_when_nothing_found(tmp_path, monkeypatch):
    """验证全部候选都未命中时 source 为 fallback，且回退到项目根的应用目录"""
    root, _, _ = _isolate_bases(tmp_path, monkeypatch)

    report = paths.probe_report()

    assert report["source"] == "fallback"
    assert report["exists"] is False
    assert report["config_path"] == str(
        root / paths.APP_DIR_NAME / paths.CONFIG_FILE_NAMES[0]
    )
    assert all(item["matched_file"] == "" for item in report["bases"])


def test_probe_report_ignores_env_var(tmp_path, monkeypatch):
    """验证 paths 层不认识 JENKINS_MCP_CONFIG（env_var 一态由 MCP 层覆写）"""
    root, _, _ = _isolate_bases(tmp_path, monkeypatch)
    expected = root / "jenkins-config.yaml"
    expected.write_text("server: {}", encoding="utf-8")

    env_config = tmp_path / "elsewhere" / "jenkins-config.yaml"
    env_config.parent.mkdir()
    env_config.write_text("server: {}", encoding="utf-8")
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(env_config))

    report = paths.probe_report()

    assert report["source"] == "probed"
    assert report["config_path"] == str(expected)


def test_probe_report_relative_arg_anchors_to_base(tmp_path, monkeypatch):
    """验证相对路径参数按候选目录锚定，仍记为 explicit_arg"""
    _, cwd, _ = _isolate_bases(tmp_path, monkeypatch)
    expected = cwd / "custom.yaml"
    expected.write_text("server: {}", encoding="utf-8")

    report = paths.probe_report("custom.yaml")

    assert report["source"] == "explicit_arg"
    assert report["config_path"] == str(expected)
    assert report["exists"] is True
