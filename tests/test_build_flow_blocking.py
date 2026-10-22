"""阻塞构建接缝测试 - run_blocking / select_rebuild_jobs"""

import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jenkins_config.build_flow import (
    run_blocking,
    select_rebuild_jobs,
)
from jenkins_config.config import Config, Job
from jenkins_config.config_types import (
    BuildConfig,
    Environment,
    Project,
    ServerConfig,
)
from jenkins_config.history import BuildRecord, HistoryManager
from jenkins_config.jenkins import BuildStatus, JenkinsClient


def _config(**build_kwargs) -> Config:
    build_params = {"build_timeout": 60, "poll_interval": 1, **build_kwargs}
    config = Config(
        server=ServerConfig(url="http://jenkins:8080", token="token"),
        build=BuildConfig(**build_params),
        environments={
            "dev": Environment(
                name="dev",
                projects=[Project(name="app", params={"branch": "develop"})],
            )
        },
    )
    return config


def _job(key="dev_app", path="app-job", env="dev", branch="develop") -> Job:
    return Job(
        key=key,
        path=path,
        branch=branch,
        params={"branch": branch},
        env=env,
        project_name="app",
    )


@pytest.fixture
def mock_client():
    client = Mock(spec=JenkinsClient)
    client.trigger_build.return_value = ("http://localhost/queue/item/1/", "")
    client.base_url = "http://localhost:8080"
    client.get_build_number.return_value = 123
    client.get_build_status.return_value = Mock(
        status=BuildStatus.SUCCESS, duration=60
    )
    client.get_build_log.return_value = "Build log content"
    return client


def test_run_blocking_success(mock_client, tmp_path):
    outcome = run_blocking(mock_client, _config(), [_job()], str(tmp_path))

    assert len(outcome.results) == 1
    assert outcome.results[0].status == BuildStatus.SUCCESS
    assert outcome.results[0].build_num == 123
    assert outcome.results[0].error is None
    assert outcome.history_error == ""


def test_run_blocking_sequential_keeps_job_order(mock_client, tmp_path):
    jobs = [_job("dev_a", "job-a"), _job("dev_b", "job-b")]

    outcome = run_blocking(
        mock_client, _config(), jobs, str(tmp_path), mode="sequential"
    )

    assert [r.job_key for r in outcome.results] == ["dev_a", "dev_b"]


def test_run_blocking_parallel_runs_all_jobs(mock_client, tmp_path):
    jobs = [_job("dev_a", "job-a"), _job("dev_b", "job-b")]

    outcome = run_blocking(mock_client, _config(), jobs, str(tmp_path))

    assert sorted(r.job_key for r in outcome.results) == ["dev_a", "dev_b"]


# ============================================================================
# 失败路径
# ============================================================================


def test_run_blocking_trigger_failure(mock_client, tmp_path):
    mock_client.trigger_build.return_value = (
        None,
        "请求URL: http://localhost:8080/job/bad-job/buildWithParameters\n"
        "状态码: 404\n响应内容: Not Found",
    )

    outcome = run_blocking(mock_client, _config(), [_job(path="bad-job")], str(tmp_path))
    result = outcome.results[0]

    assert result.status == BuildStatus.FAILURE
    assert result.build_num == 0
    assert result.error == "触发构建失败"
    assert result.log_file.endswith("_error.log")


def test_run_blocking_queue_timeout_uses_config(mock_client, tmp_path):
    mock_client.get_build_number.return_value = None
    config = _config(queue_timeout=120)

    outcome = run_blocking(mock_client, config, [_job(path="slow-job")], str(tmp_path))
    result = outcome.results[0]

    assert mock_client.get_build_number.call_args.kwargs["timeout"] == 120
    assert result.status == BuildStatus.TIMEOUT
    assert "120秒" in Path(result.log_file).read_text(encoding="utf-8")


def test_run_blocking_aborted(mock_client, tmp_path):
    mock_client.get_build_status.return_value = Mock(
        status=BuildStatus.ABORTED, duration=10
    )
    mock_client.get_build_log.return_value = "Aborted by user"

    outcome = run_blocking(mock_client, _config(), [_job(path="aborted-job")], str(tmp_path))

    assert outcome.results[0].status == BuildStatus.ABORTED
    assert outcome.results[0].build_num == 123


def test_run_blocking_empty_log_for_failure(mock_client, tmp_path):
    mock_client.get_build_log.return_value = ""
    mock_client.get_build_status.return_value = Mock(
        status=BuildStatus.FAILURE, duration=10
    )

    outcome = run_blocking(mock_client, _config(), [_job(path="fail-job")], str(tmp_path))
    content = Path(outcome.results[0].log_file).read_text(encoding="utf-8")

    assert "构建日志获取失败或为空" in content
    assert "可能原因" in content
    assert "Jenkins 控制台" in content


def test_run_blocking_failure_saves_error_lines(mock_client, tmp_path):
    mock_client.get_build_log.return_value = (
        "some output\n"
        "make: *** [build] Error 1\n"
        "npm ERR! code ELIFECYCLE\n"
        "normal line\n"
    )
    mock_client.get_build_status.return_value = Mock(
        status=BuildStatus.FAILURE, duration=10
    )

    outcome = run_blocking(mock_client, _config(), [_job(path="fail-job")], str(tmp_path))
    saved_log = Path(outcome.results[0].log_file).read_text(encoding="utf-8")

    assert "make:" in saved_log
    assert "npm ERR!" in saved_log
    assert "normal line" in saved_log


def test_run_blocking_waits_through_building(mock_client, tmp_path):
    status_building = Mock(status=BuildStatus.BUILDING, duration=10)
    status_success = Mock(status=BuildStatus.SUCCESS, duration=30)
    mock_client.get_build_status.side_effect = [
        status_building,
        status_success,
        status_success,
    ]
    mock_client.get_build_log.return_value = "build output"

    with patch.object(time, "sleep"):
        outcome = run_blocking(mock_client, _config(), [_job(path="build-job")], str(tmp_path))

    assert outcome.results[0].status == BuildStatus.SUCCESS
    assert outcome.results[0].duration == 30


def test_run_blocking_three_unknown_statuses_fail(mock_client, tmp_path):
    mock_client.get_build_status.return_value = Mock(
        status=BuildStatus.UNKNOWN, duration=0
    )

    with patch("jenkins_config.build_flow.time.sleep"):
        outcome = run_blocking(mock_client, _config(), [_job()], str(tmp_path))

    assert outcome.results[0].status == BuildStatus.FAILURE


def test_run_blocking_build_timeout(mock_client, tmp_path):
    mock_client.get_build_status.return_value = Mock(
        status=BuildStatus.BUILDING, duration=0
    )
    config = _config(build_timeout=1, poll_interval=3600)

    with patch("jenkins_config.build_flow.time.time", side_effect=[0, 2]):
        outcome = run_blocking(mock_client, config, [_job(path="timeout-job")], str(tmp_path))

    assert outcome.results[0].status == BuildStatus.TIMEOUT


# ============================================================================
# 历史记录
# ============================================================================


def test_run_blocking_writes_one_batch_timestamp(mock_client, tmp_path):
    history_file = tmp_path / "data" / "build_history.json"
    history_file.parent.mkdir()
    mock_client.trigger_build.side_effect = [
        ("http://localhost/queue/item/1/", ""),
        (None, "404 Not Found"),
    ]
    jobs = [_job("dev_ok", "job-ok"), _job("dev_bad", "job-bad")]

    outcome = run_blocking(
        mock_client,
        _config(),
        jobs,
        str(tmp_path),
        mode="sequential",
        history_file=str(history_file),
    )

    assert outcome.history_error == ""
    records = HistoryManager(str(history_file)).list()
    assert len(records) == 2
    assert records[0].timestamp == records[1].timestamp
    statuses = {record.job_key: record.status for record in records}
    assert statuses == {"dev_ok": "SUCCESS", "dev_bad": "FAILURE"}
    for record in records:
        assert record.env == "dev"
        assert record.project_name == "app"
        assert record.job_path in ("job-ok", "job-bad")


def test_run_blocking_reports_history_failure(mock_client, tmp_path):
    history_file = tmp_path / "build_history.json"

    with patch(
        "jenkins_config.history.HistoryManager.add_batch",
        side_effect=OSError("disk full"),
    ):
        outcome = run_blocking(
            mock_client,
            _config(),
            [_job()],
            str(tmp_path),
            history_file=str(history_file),
        )

    assert outcome.history_error == "disk full"
    assert len(outcome.results) == 1
    assert outcome.results[0].status == BuildStatus.SUCCESS


# ============================================================================
# 重建选择
# ============================================================================


def test_select_rebuild_jobs_pairs_records_and_reports_missing():
    config = _config()
    found = BuildRecord(
        timestamp="2026-10-20T10:00:00",
        env="dev",
        job_key="dev_app",
        build_num=42,
        status="SUCCESS",
        duration=1,
        log_file="",
        project_name="app",
    )
    missing = BuildRecord(
        timestamp="2026-10-20T10:00:00",
        env="dev",
        job_key="dev_ghost",
        build_num=43,
        status="SUCCESS",
        duration=1,
        log_file="",
        project_name="ghost",
    )

    selection = select_rebuild_jobs(config, [found, missing])

    assert [job.key for job in selection.jobs] == ["dev_app"]
    assert selection.records == [found]
    assert selection.missing == [missing]
