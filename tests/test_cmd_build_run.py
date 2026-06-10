"""构建执行/重建命令测试 - 从 test_cmd_build.py 拆分"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jenkins_config.cmd_build import run_build, run_rebuild_last
from jenkins_config.build_result import BuildResult
from jenkins_config.jenkins import BuildStatus


# ============================================================================
# run_build
# ============================================================================


def test_run_build_success(tmp_path, capsys):
    """正常构建流程"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.mode = "parallel"
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

        run_build(config_file, args)

    captured = capsys.readouterr()
    assert "Jenkins 自动构建脚本" in captured.err
    assert "dev_app" in captured.out or "dev_app" in captured.err


def test_run_build_config_not_found(tmp_path):
    """配置文件不存在时退出"""
    config_file = tmp_path / "nonexistent.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()

    with patch("jenkins_config.cmd_build.Config.load",
               side_effect=FileNotFoundError("not found")):
        with pytest.raises(SystemExit) as exc:
            run_build(config_file, args)
        assert exc.value.code == 1


def test_run_build_no_jobs(tmp_path):
    """没有匹配的 Job 时退出"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()

    with patch("jenkins_config.cmd_build.Config.load") as mock_load:
        mock_config = MagicMock()
        mock_config.get_jobs.return_value = []
        mock_load.return_value = mock_config
        with pytest.raises(SystemExit) as exc:
            run_build(config_file, args)
        assert exc.value.code == 1


def test_run_build_with_branch_override(tmp_path):
    """CLI -b 覆盖分支"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.mode = "parallel"
    args.env = "dev"
    args.jobs = None
    args.branch = "hotfix"
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
                             log_file="", branch="hotfix",
                             params={"branch": "hotfix"}, project_name="app")
        mock_builder.build_parallel.return_value = [result]

        run_build(config_file, args)

    assert job.branch == "hotfix"
    assert job.params["branch"] == "hotfix"


def test_run_build_with_params_override(tmp_path):
    """CLI -p 覆盖参数"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("dummy", encoding="utf-8")
    args = MagicMock()
    args.mode = "parallel"
    args.env = "dev"
    args.jobs = None
    args.branch = None
    args.params = "APP_VERSION=2.0&SKIP_TESTS=true"
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
                             params={"branch": "develop", "APP_VERSION": "2.0",
                                     "SKIP_TESTS": "true"},
                             project_name="app")
        mock_builder.build_parallel.return_value = [result]

        run_build(config_file, args)

    assert job.params["APP_VERSION"] == "2.0"
    assert job.params["SKIP_TESTS"] == "true"


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
