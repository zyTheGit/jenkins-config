"""
构建命令模块测试

测试覆盖：
- _cleanup_old_logs 日志清理
- generate_report 报告生成
- run_build 构建执行流程
- run_rebuild_last 重建流程
"""

import io
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, ANY

import pytest

from jenkins_config.cmd_build import _cleanup_old_logs, generate_report, run_build, run_rebuild_last
from jenkins_config.build_result import BuildResult
from jenkins_config.jenkins import BuildStatus


def _create_log_dir(root: Path, days_ago: int, name: str = None) -> Path:
    """创建指定天数的旧日志目录"""
    date_str = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
    dir_name = name or f"build_{date_str}"
    d = root / dir_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "test.log").write_text("log content")
    return d


def test_cleanup_removes_old_logs(tmp_path):
    """清理超过保留天数的日志目录"""
    _create_log_dir(tmp_path, days_ago=1)   # 1 天前 → 保留
    _create_log_dir(tmp_path, days_ago=5)   # 5 天前 → 删除
    _create_log_dir(tmp_path, days_ago=10)  # 10 天前 → 删除

    _cleanup_old_logs(str(tmp_path), retention_days=3)

    dirs = sorted(d.name for d in tmp_path.iterdir() if d.is_dir())
    # 只有 1 天前的保留（5天和10天被删）
    assert len(dirs) == 1


def test_cleanup_keeps_all_within_retention(tmp_path):
    """保留期内的日志全部保留"""
    _create_log_dir(tmp_path, days_ago=0)
    _create_log_dir(tmp_path, days_ago=1)
    _create_log_dir(tmp_path, days_ago=2)

    _cleanup_old_logs(str(tmp_path), retention_days=3)

    dirs = [d.name for d in tmp_path.iterdir() if d.is_dir()]
    assert len(dirs) == 3


def test_cleanup_skips_non_build_dirs(tmp_path):
    """非 build_ 前缀的目录不受影响"""
    _create_log_dir(tmp_path, days_ago=10, name="build_old")
    (tmp_path / "other_dir").mkdir()
    (tmp_path / ".keep").mkdir()

    _cleanup_old_logs(str(tmp_path), retention_days=3)

    dirs = sorted(d.name for d in tmp_path.iterdir() if d.is_dir())
    assert "other_dir" in dirs
    assert ".keep" in dirs
    # build_old 日期格式不合法，cleanup 会跳过（不删）
    assert "build_old" in dirs


def test_cleanup_non_existent_dir(tmp_path):
    """不存在的目录不报错"""
    _cleanup_old_logs(str(tmp_path / "nonexistent"), retention_days=3)
    # 不抛异常即通过


def test_cleanup_skips_files(tmp_path):
    """跳过目录中的普通文件"""
    f = tmp_path / "build_something.txt"
    f.write_text("not a dir")
    _cleanup_old_logs(str(tmp_path), retention_days=3)
    assert f.exists()


def test_cleanup_skips_invalid_date_format(tmp_path):
    """目录名日期格式不合法时跳过"""
    d = tmp_path / "build_abc"
    d.mkdir()
    _cleanup_old_logs(str(tmp_path), retention_days=3)
    assert d.exists()


# ============================================================================
# generate_report
# ============================================================================


def _make_result(job_key, status, build_num=1):
    """辅助创建 BuildResult"""
    return BuildResult(
        job_key=job_key, build_num=build_num, status=status,
        duration=60, log_file="/tmp/build.log",
    )


def test_generate_report_all_success(capsys):
    """全部成功"""
    results = [
        _make_result("dev_app", BuildStatus.SUCCESS, 1),
        _make_result("dev_web", BuildStatus.SUCCESS, 2),
    ]
    generate_report(results, "/tmp/logs")
    captured = capsys.readouterr()
    assert "构建结果汇总" in captured.err
    assert "总计: 2 个 Job" in captured.out
    assert "成功: 2 个" in captured.out
    assert "失败: 0 个" in captured.out
    assert "[OK] dev_app" in captured.out
    assert "[OK] dev_web" in captured.out


def test_generate_report_mixed(capsys):
    """混合成功/失败/中止/超时 — 有失败时 sys.exit(1)"""
    results = [
        _make_result("ok_job", BuildStatus.SUCCESS, 10),
        _make_result("fail_job", BuildStatus.FAILURE, 0),
        _make_result("abort_job", BuildStatus.ABORTED, 5),
        _make_result("timeout_job", BuildStatus.TIMEOUT, 0),
    ]
    with pytest.raises(SystemExit) as exc:
        generate_report(results, "/tmp/logs")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "成功: 1 个" in captured.out
    assert "失败: 3 个" in captured.out
    assert "[FAIL] fail_job" in captured.out
    assert "[ABORT] abort_job" in captured.out
    assert "[TIMEOUT] timeout_job" in captured.out


def test_generate_report_exits_on_failure():
    """有失败构建时退出码为 1"""
    results = [_make_result("bad_job", BuildStatus.FAILURE)]
    with pytest.raises(SystemExit) as exc:
        generate_report(results, "/tmp/logs")
    assert exc.value.code == 1


# ============================================================================
# run_build
# ============================================================================


@pytest.fixture
def mock_args():
    args = MagicMock()
    args.mode = "parallel"
    args.env = "dev"
    args.jobs = None
    args.branch = None
    args.params = None
    args.yes = True
    return args


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.branch_field = "branch"
    config.server.url = "http://jenkins:8080"
    config.server.username = "admin"
    config.server.token = "token"
    config.build.mode = "parallel"
    config.build.poll_interval = 5
    config.build.build_timeout = 3600
    config.build.curl_timeout = 30
    config.build.log_dir = "/tmp/logs"
    config.build.log_retention_days = 3
    return config


def test_run_build_success(tmp_path, mock_args, capsys):
    """正常构建流程"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.JenkinsClient") as mock_client_cls,
        patch("jenkins_config.cmd_build.Builder") as mock_builder_cls,
        patch("jenkins_config.cmd_build.HistoryManager") as mock_hist_cls,
        patch("jenkins_config.cmd_build._cleanup_old_logs"),
    ):
        mock_config = MagicMock()
        mock_config.branch_field = "branch"
        mock_config.build.log_dir = str(tmp_path)
        mock_config.build.log_retention_days = 3
        mock_config.server = MagicMock(url="http://jenkins:8080",
                                        username="admin", token="token")
        mock_load.return_value = mock_config

        job = MagicMock()
        job.key = "dev_app"
        job.env = "dev"
        job.path = "app-job"
        job.branch = "main"
        job.params = {}
        mock_config.get_jobs.return_value = [job]

        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder

        result = BuildResult(job_key="dev_app", build_num=42,
                             status=BuildStatus.SUCCESS, duration=60,
                             log_file="/tmp/log", branch="main",
                             params={}, project_name="app")
        mock_builder.build_parallel.return_value = [result]

        run_build(config_file, mock_args)

    captured = capsys.readouterr()
    assert "Jenkins 自动构建脚本" in captured.err
    assert "dev_app" in captured.out or "dev_app" in captured.err


def test_run_build_config_not_found(tmp_path, mock_args):
    """配置文件不存在时退出"""
    config_file = tmp_path / "nonexistent.yaml"
    config_file.write_text("dummy", encoding="utf-8")

    with patch("jenkins_config.cmd_build.Config.load",
               side_effect=FileNotFoundError("not found")):
        with pytest.raises(SystemExit) as exc:
            run_build(config_file, mock_args)
        assert exc.value.code == 1


def test_run_build_no_jobs(tmp_path, mock_args):
    """没有匹配的 Job 时退出"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")

    with patch("jenkins_config.cmd_build.Config.load") as mock_load:
        mock_config = MagicMock()
        mock_config.get_jobs.return_value = []
        mock_load.return_value = mock_config
        with pytest.raises(SystemExit) as exc:
            run_build(config_file, mock_args)
        assert exc.value.code == 1


def test_run_build_with_branch_override(tmp_path, mock_args, capsys):
    """CLI -b 覆盖分支"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    mock_args.branch = "hotfix"

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.JenkinsClient"),
        patch("jenkins_config.cmd_build.Builder") as mock_builder_cls,
        patch("jenkins_config.cmd_build.HistoryManager"),
        patch("jenkins_config.cmd_build._cleanup_old_logs"),
    ):
        mock_config = MagicMock()
        mock_config.branch_field = "branch"
        mock_config.build.log_dir = str(tmp_path)
        mock_config.build.log_retention_days = 3
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        job = MagicMock()
        job.key = "dev_app"
        job.env = "dev"
        job.params = {"branch": "develop"}
        mock_config.get_jobs.return_value = [job]

        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        result = BuildResult(job_key="dev_app", build_num=42,
                             status=BuildStatus.SUCCESS, duration=60,
                             log_file="", branch="hotfix",
                             params={"branch": "hotfix"}, project_name="app")
        mock_builder.build_parallel.return_value = [result]

        run_build(config_file, mock_args)

    assert job.branch == "hotfix"
    assert job.params["branch"] == "hotfix"


def test_run_build_with_params_override(tmp_path, mock_args, capsys):
    """CLI -p 覆盖参数"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    mock_args.params = "APP_VERSION=2.0&SKIP_TESTS=true"

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.JenkinsClient"),
        patch("jenkins_config.cmd_build.Builder") as mock_builder_cls,
        patch("jenkins_config.cmd_build.HistoryManager"),
        patch("jenkins_config.cmd_build._cleanup_old_logs"),
    ):
        mock_config = MagicMock()
        mock_config.branch_field = "branch"
        mock_config.build.log_dir = str(tmp_path)
        mock_config.build.log_retention_days = 3
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        job = MagicMock()
        job.key = "dev_app"
        job.env = "dev"
        job.params = {"branch": "develop"}
        mock_config.get_jobs.return_value = [job]

        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        result = BuildResult(job_key="dev_app", build_num=42,
                             status=BuildStatus.SUCCESS, duration=60,
                             log_file="", branch="develop",
                             params={"branch": "develop", "APP_VERSION": "2.0",
                                     "SKIP_TESTS": "true"},
                             project_name="app")
        mock_builder.build_parallel.return_value = [result]

        run_build(config_file, mock_args)

    assert job.params["APP_VERSION"] == "2.0"
    assert job.params["SKIP_TESTS"] == "true"


def test_run_build_sequential_mode(tmp_path, capsys):
    """顺序构建模式"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.mode = "sequential"
    args.env = "dev"
    args.jobs = None
    args.branch = None
    args.params = None
    args.yes = True

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.JenkinsClient"),
        patch("jenkins_config.cmd_build.Builder") as mock_builder_cls,
        patch("jenkins_config.cmd_build.HistoryManager"),
        patch("jenkins_config.cmd_build._cleanup_old_logs"),
    ):
        mock_config = MagicMock()
        mock_config.branch_field = "branch"
        mock_config.build.log_dir = str(tmp_path)
        mock_config.build.log_retention_days = 3
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        job = MagicMock()
        job.key = "dev_app"
        job.env = "dev"
        job.params = {"branch": "develop"}
        mock_config.get_jobs.return_value = [job]

        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        result = BuildResult(job_key="dev_app", build_num=42,
                             status=BuildStatus.SUCCESS, duration=60,
                             log_file="", branch="develop",
                             params={}, project_name="app")
        mock_builder.build_sequential.return_value = [result]

        run_build(config_file, args)

    mock_builder.build_sequential.assert_called_once()


# ============================================================================
# run_rebuild_last
# ============================================================================


def test_run_rebuild_last_success(tmp_path, capsys):
    """重建上次构建"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.HistoryManager") as mock_hist_cls,
        patch("jenkins_config.cmd_build.JenkinsClient"),
        patch("jenkins_config.cmd_build.Builder") as mock_builder_cls,
        patch("jenkins_config.cmd_build._cleanup_old_logs"),
    ):
        mock_config = MagicMock()
        mock_config.build.log_dir = str(tmp_path)
        mock_config.build.log_retention_days = 3
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        from jenkins_config.history import BuildRecord
        record = BuildRecord(timestamp="2026-06-09T10:00:00", env="dev",
                             job_key="dev_app", build_num=42,
                             status="SUCCESS", duration=60, log_file="",
                             project_name="app", params={"branch": "main"})
        mock_mgr = MagicMock()
        mock_mgr.get_last_build_group.return_value = [record]
        mock_hist_cls.return_value = mock_mgr

        job = MagicMock()
        job.key = "dev_app"
        job.env = "dev"
        mock_config.create_job_from_record.return_value = job

        args = MagicMock()
        args.mode = "parallel"
        args.yes = True

        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        result = BuildResult(job_key="dev_app", build_num=43,
                             status=BuildStatus.SUCCESS, duration=60,
                             log_file="", branch="main", params={},
                             project_name="app")
        mock_builder.build_parallel.return_value = [result]

        run_rebuild_last(config_file, args)

    captured = capsys.readouterr()
    assert "重建上次构建" in captured.err
    assert "dev_app" in captured.out


def test_run_rebuild_last_no_history(tmp_path):
    """无历史记录时退出"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.HistoryManager") as mock_hist_cls,
    ):
        mock_config = MagicMock()
        mock_load.return_value = mock_config

        mock_mgr = MagicMock()
        mock_mgr.get_last_build_group.return_value = None
        mock_hist_cls.return_value = mock_mgr

        args = MagicMock()
        with pytest.raises(SystemExit) as exc:
            run_rebuild_last(config_file, args)
        assert exc.value.code == 1


def test_run_rebuild_last_no_jobs(tmp_path):
    """历史记录的项目不在配置中时退出"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.HistoryManager") as mock_hist_cls,
    ):
        mock_config = MagicMock()
        mock_config.build.log_dir = str(tmp_path)
        mock_config.build.log_retention_days = 3
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        from jenkins_config.history import BuildRecord
        record = BuildRecord(timestamp="", env="dev", job_key="dev_ghost",
                             build_num=1, status="SUCCESS", duration=0,
                             log_file="", project_name="ghost",
                             params={"branch": "main"})
        mock_mgr = MagicMock()
        mock_mgr.get_last_build_group.return_value = [record]
        mock_hist_cls.return_value = mock_mgr

        mock_config.create_job_from_record.return_value = None

        args = MagicMock()
        args.yes = True

        with pytest.raises(SystemExit) as exc:
            run_rebuild_last(config_file, args)
        assert exc.value.code == 1


# ============================================================================
# build/rebuild 确认提示
# ============================================================================


def test_run_build_user_cancels(tmp_path):
    """用户取消构建"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.mode = "parallel"
    args.env = "dev"
    args.yes = False
    args.jobs = None
    args.branch = None
    args.params = None

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("builtins.input", return_value="n"),
    ):
        mock_config = MagicMock()
        mock_config.branch_field = "branch"
        mock_config.build = MagicMock()
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config
        mock_config.get_jobs.return_value = [MagicMock()]

        with pytest.raises(SystemExit) as exc:
            run_build(config_file, args)
        assert exc.value.code == 0


def test_run_build_keyboard_interrupt(tmp_path):
    """用户 Ctrl+C 取消构建"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.mode = "parallel"
    args.env = "dev"
    args.yes = False
    args.jobs = None
    args.branch = None
    args.params = None

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("builtins.input", side_effect=KeyboardInterrupt()),
    ):
        mock_config = MagicMock()
        mock_config.branch_field = "branch"
        mock_config.build = MagicMock()
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config
        mock_config.get_jobs.return_value = [MagicMock()]

        with pytest.raises(SystemExit) as exc:
            run_build(config_file, args)
        assert exc.value.code == 130


def test_run_build_yes_skip_confirm(tmp_path):
    """-y 跳过确认直接构建"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.mode = "parallel"
    args.env = "dev"
    args.yes = True
    args.jobs = None
    args.branch = None
    args.params = None

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.JenkinsClient"),
        patch("jenkins_config.cmd_build.Builder") as mock_builder_cls,
        patch("jenkins_config.cmd_build.HistoryManager"),
        patch("jenkins_config.cmd_build._cleanup_old_logs"),
    ):
        mock_config = MagicMock()
        mock_config.branch_field = "branch"
        mock_config.build.log_dir = str(tmp_path)
        mock_config.build.log_retention_days = 3
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        job = MagicMock()
        job.key = "dev_app"
        job.env = "dev"
        job.params = {}
        mock_config.get_jobs.return_value = [job]

        mock_builder = MagicMock()
        mock_builder_cls.return_value = mock_builder
        mock_builder.build_parallel.return_value = [
            BuildResult(job_key="dev_app", build_num=42,
                        status=BuildStatus.SUCCESS, duration=60,
                        log_file="", branch="main", params={}, project_name="app")
        ]

        run_build(config_file, args)

    mock_builder.build_parallel.assert_called_once()


def test_generate_report_unknown_status(capsys):
    """未知状态显示 [?]"""
    class UnknownStatus:
        value = "UNKNOWN_STATUS"
    results = [BuildResult(
        job_key="weird_job", build_num=99, status=UnknownStatus(),
        duration=0, log_file="",
    )]
    with pytest.raises(SystemExit):
        generate_report(results, "/tmp/logs")
    captured = capsys.readouterr()
    assert "[?] weird_job: UNKNOWN_STATUS (#99)" in captured.out


# ============================================================================
# rebuild 确认提示
# ============================================================================


def test_run_rebuild_last_user_cancels(tmp_path):
    """重建时用户取消"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.yes = False

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.HistoryManager") as mock_hist_cls,
        patch("builtins.input", return_value="n"),
    ):
        mock_config = MagicMock()
        mock_config.build = MagicMock()
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        from jenkins_config.history import BuildRecord
        record = BuildRecord(timestamp="2026-06-09T10:00:00", env="dev",
                             job_key="dev_app", build_num=42,
                             status="SUCCESS", duration=60, log_file="",
                             project_name="app", params={"branch": "main"})
        mock_mgr = MagicMock()
        mock_mgr.get_last_build_group.return_value = [record]
        mock_hist_cls.return_value = mock_mgr

        mock_config.create_job_from_record.return_value = MagicMock()

        with pytest.raises(SystemExit) as exc:
            run_rebuild_last(config_file, args)
        assert exc.value.code == 0


def test_run_rebuild_last_keyboard_interrupt(tmp_path):
    """重建时 Ctrl+C"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.yes = False

    with (
        patch("jenkins_config.cmd_build.Config.load") as mock_load,
        patch("jenkins_config.cmd_build.HistoryManager") as mock_hist_cls,
        patch("builtins.input", side_effect=KeyboardInterrupt()),
    ):
        mock_config = MagicMock()
        mock_config.build = MagicMock()
        mock_config.server = MagicMock()
        mock_load.return_value = mock_config

        from jenkins_config.history import BuildRecord
        record = BuildRecord(timestamp="", env="dev", job_key="dev_app",
                             build_num=42, status="SUCCESS", duration=60,
                             log_file="", project_name="app", params={})
        mock_mgr = MagicMock()
        mock_mgr.get_last_build_group.return_value = [record]
        mock_hist_cls.return_value = mock_mgr

        mock_config.create_job_from_record.return_value = MagicMock()

        with pytest.raises(SystemExit) as exc:
            run_rebuild_last(config_file, args)
        assert exc.value.code == 130
