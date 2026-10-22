"""队列触发接缝测试 - trigger_queued / 编号探测 / BUILDING 占位记录"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from jenkins_config.build_flow import _probe_build_numbers, trigger_queued
from jenkins_config.config_types import Job
from jenkins_config.history import HistoryManager


def _job(
    key="dev_app", path="app-job", env="dev", branch="develop", project_name="app"
) -> Job:
    return Job(
        key=key,
        path=path,
        branch=branch,
        params={"branch": branch},
        env=env,
        project_name=project_name,
    )


def _client():
    client = Mock()
    client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
    client.get_build_number.return_value = 123
    return client


def test_trigger_queued_collects_success_and_failures():
    """触发成功、未返回队列 URL、触发异常分别进入 triggered / failed"""
    jobs = [_job(key="dev_a"), _job(key="dev_b"), _job(key="dev_c")]
    client = _client()
    client.trigger_build.side_effect = [
        ("http://jenkins/queue/item/1/", ""),
        (None, "状态码: 404"),
        RuntimeError("connection reset"),
    ]

    outcome = trigger_queued(client, jobs, wait_build_num=False)

    assert [item.job_key for item in outcome.triggered] == ["dev_a"]
    assert outcome.triggered[0].queue_url == "http://jenkins/queue/item/1/"
    assert outcome.triggered[0].build_num is None
    assert [item.job_key for item in outcome.failed] == ["dev_b", "dev_c"]
    assert "触发失败" in outcome.failed[0].error
    assert "404" in outcome.failed[0].error
    assert "触发异常" in outcome.failed[1].error


def test_trigger_queued_skips_probe_when_disabled():
    """wait_build_num=False 时不探测编号"""
    client = _client()

    outcome = trigger_queued(client, [_job()], wait_build_num=False)

    client.get_build_number.assert_not_called()
    assert outcome.triggered[0].build_num is None


def test_trigger_queued_probe_uses_given_budget():
    """编号探测的 timeout 由调用方预算决定"""
    client = _client()

    trigger_queued(client, [_job()], probe_timeout=10)

    assert client.get_build_number.call_args.kwargs["timeout"] == 10


def test_trigger_queued_keeps_trigger_order_when_probing():
    """并发探测后 triggered 仍保持触发顺序"""
    jobs = [_job(key="dev_a", path="job-a"), _job(key="dev_b", path="job-b")]
    client = _client()
    client.get_build_number.side_effect = [11, 22]

    outcome = trigger_queued(client, jobs)

    assert [item.job_key for item in outcome.triggered] == ["dev_a", "dev_b"]
    assert [item.build_num for item in outcome.triggered] == [11, 22]


def test_probe_build_numbers_survives_exception():
    """单个探测异常不影响整批结果，对应位置返回 None"""
    client = Mock()
    client.get_build_number.side_effect = [Exception("boom"), 7]
    queued = [(Mock(), "http://q/1/"), (Mock(), "http://q/2/")]

    assert _probe_build_numbers(client, queued, 1) == [None, 7]


def test_trigger_queued_writes_building_placeholder(tmp_path):
    """成功入队后写入 BUILDING 占位记录，编号未分配时 build_num=0"""
    hist_file = str(tmp_path / "build_history.json")
    client = _client()
    client.get_build_number.return_value = None
    job = _job()

    outcome = trigger_queued(client, [job], history_file=hist_file)

    assert outcome.triggered[0].build_num is None
    records = HistoryManager(hist_file).list(limit=10)
    assert len(records) == 1
    record = records[0]
    assert record.build_num == 0
    assert record.status == "BUILDING"
    assert record.job_key == "dev_app"
    assert record.env == "dev"
    assert record.branch == "develop"
    assert record.project_name == "app"
    assert record.job_path == "app-job"
    assert record.params == {"branch": "develop"}


def test_trigger_queued_uses_custom_branch_field(tmp_path):
    """写历史时通过 branch_field_for 按环境解析分支参数名"""
    hist_file = str(tmp_path / "build_history.json")
    client = _client()
    job = _job()
    job.params = {"BRANCH_NAME": "feat/x"}

    trigger_queued(
        client,
        [job],
        history_file=hist_file,
        branch_field_for=lambda env: "BRANCH_NAME",
    )

    records = HistoryManager(hist_file).list(limit=10)
    assert records[0].branch == "feat/x"


def test_trigger_queued_derives_env_from_job_key(tmp_path):
    """Job 缺少 env 时（直连模式）从 job_key 推导环境名"""
    hist_file = str(tmp_path / "build_history.json")
    client = _client()
    job = SimpleNamespace(
        key="test_app", path="app-job", params={"branch": "main"},
        project_name="app",
    )

    trigger_queued(client, [job], history_file=hist_file, wait_build_num=False)

    records = HistoryManager(hist_file).list(limit=10)
    assert records[0].env == "test"


def test_trigger_queued_reports_history_failure():
    """历史写入失败时返回 history_error，已触发结果不受影响"""
    client = _client()

    with patch("jenkins_config.history.HistoryManager") as MockHistory:
        MockHistory.return_value.add_batch.side_effect = OSError("disk full")
        outcome = trigger_queued(client, [_job()], history_file="any.json")

    assert len(outcome.triggered) == 1
    assert outcome.history_error == "disk full"


def test_trigger_queued_does_not_write_history_without_success():
    """一个都没触发成功时不写历史"""
    client = _client()
    client.trigger_build.return_value = (None, "404")

    with patch("jenkins_config.history.HistoryManager") as MockHistory:
        outcome = trigger_queued(client, [_job()], history_file="any.json")

    assert outcome.triggered == []
    MockHistory.assert_not_called()
