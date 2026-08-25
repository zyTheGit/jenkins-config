# tests/test_mcp/test_build_tools.py
"""
MCP 构建操作工具测试

测试覆盖：
- trigger_build 触发成功场景
- trigger_build 部分失败场景
- rebuild_last 正常流程
- 空环境名的错误处理
- 写操作开关与直连模式白名单
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jenkins_config.config_types import BuildConfig, Job, ServerConfig
from jenkins_config.mcp.utils import ALLOWED_HOSTS_ENV_VAR, WRITE_ENV_VAR


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def allow_write(monkeypatch):
    """默认放开写操作与直连白名单，便于验证业务逻辑本身"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")
    monkeypatch.setenv(ALLOWED_HOSTS_ENV_VAR, "jenkins")


@pytest.fixture
def mock_config():
    """创建 mock Config 实例"""
    config = Mock()  # 不使用 spec，因为 Config 的方法是猴子补丁添加的
    config.server = ServerConfig(
        url="http://jenkins:8080",
        username="admin",
        token="test-token",
    )
    config.build = BuildConfig(curl_timeout=30)
    config.branch_field = "branch"
    config.branch_field_for.return_value = "branch"
    return config


@pytest.fixture
def sample_jobs():
    """创建示例 Job 列表"""
    return [
        Job(
            key="dev_project_a", path="project-a", branch="develop",
            params={"branch": "develop"}, env="dev", project_name="project-a",
        ),
        Job(
            key="dev_project_b", path="project-b", branch="develop",
            params={"branch": "develop"}, env="dev", project_name="project-b",
        ),
    ]


# ============================================================================
# trigger_build 成功场景
# ============================================================================


def test_trigger_build_success(mock_config, sample_jobs):
    """验证 trigger_build 触发成功时返回正确的结果格式"""
    mock_config.get_jobs.return_value = sample_jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 123
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["triggered"]) == 2
        assert len(result["failed"]) == 0
        assert result["triggered"][0]["job_key"] == "dev_project_a"
        assert result["triggered"][0]["queue_url"] == "http://jenkins/queue/item/1/"
        assert result["triggered"][0]["build_num"] == 123


def test_trigger_build_denied_without_write_env(monkeypatch, mock_config):
    """验证未开启写开关时 trigger_build 直接拒绝"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)

    from jenkins_config.mcp.tools.build_tools import trigger_build
    result = trigger_build(env="dev")

    assert result["triggered"] == []
    assert WRITE_ENV_VAR in result["failed"][0]["error"]


def test_trigger_build_with_branch_override(mock_config, sample_jobs):
    """验证 trigger_build 覆盖分支参数（参数名按环境解析）"""
    mock_config.branch_field_for.return_value = "BRANCH_NAME"
    mock_config.get_jobs.return_value = sample_jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 456
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        trigger_build(env="dev", branch="feature/new-feature")

        # 环境级 branch_field 生效：写入 BRANCH_NAME 而非全局 branch
        for job in sample_jobs:
            assert job.branch == "feature/new-feature"
            assert job.params["BRANCH_NAME"] == "feature/new-feature"
        mock_config.branch_field_for.assert_called_with("dev")


def test_trigger_build_with_project_filter(mock_config):
    """验证 trigger_build 按项目过滤"""
    jobs = [Job(key="dev_project_a", path="project-a", branch="develop", params={}, env="dev")]
    mock_config.get_jobs.return_value = jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 100
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev", projects="project-a")

        # 验证 get_jobs 被调用时带过滤参数
        mock_config.get_jobs.assert_called_with(env="dev", jobs=["project-a"])
        assert len(result["triggered"]) == 1


def test_trigger_build_with_extra_params(mock_config, sample_jobs):
    """验证 trigger_build 合并额外参数"""
    mock_config.get_jobs.return_value = sample_jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 200
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        trigger_build(env="dev", params='{"SKIP_TESTS": "true"}')

        # 验证 jobs 的 params 被更新
        for job in sample_jobs:
            assert job.params["SKIP_TESTS"] == "true"


def test_trigger_build_invalid_json_params_reports_error(mock_config, sample_jobs):
    """验证非法 JSON 参数直接报错，而不是静默丢弃用户参数"""
    mock_config.get_jobs.return_value = sample_jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev", params="{invalid json}")

        assert result["triggered"] == []
        assert "参数解析失败" in result["failed"][0]["error"]


def test_trigger_build_reports_skipped_params(mock_config, sample_jobs):
    """验证非标量参数被跳过时通过 skipped_params 回传"""
    mock_config.get_jobs.return_value = sample_jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 1
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev", params='{"OK": "1", "LIST": [1, 2]}')

        assert result["skipped_params"] == ["LIST"]


def test_trigger_build_skips_probe_when_disabled(mock_config, sample_jobs):
    """验证 wait_build_num=False 时不探测编号，立即返回"""
    mock_config.get_jobs.return_value = sample_jobs[:1]

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev", wait_build_num=False)

        mock_client.get_build_number.assert_not_called()
        assert result["triggered"][0]["build_num"] is None


def test_trigger_build_reports_history_write_failure(mock_config, sample_jobs):
    """验证历史写入失败时通过 history_error 暴露，而不是静默吞掉"""
    mock_config.get_jobs.return_value = sample_jobs[:1]

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch(
             "jenkins_config.mcp.tools.build_tools._record_triggered",
             side_effect=OSError("disk full"),
         ):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 1
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["triggered"]) == 1
        assert "历史写入失败" in result["history_error"]


# ============================================================================
# trigger_build 部分失败场景
# ============================================================================


def test_trigger_build_partial_failure(mock_config, sample_jobs):
    """验证 trigger_build 部分 Job 触发失败时返回正确结果"""
    mock_config.get_jobs.return_value = sample_jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        # 第一个成功，第二个失败
        mock_client.trigger_build.side_effect = [
            ("http://jenkins/queue/item/1/", ""),
            (None, "状态码: 404"),
        ]
        mock_client.get_build_number.return_value = 123
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["triggered"]) == 1
        assert len(result["failed"]) == 1
        assert result["failed"][0]["job_key"] == "dev_project_b"
        assert "触发失败" in result["failed"][0]["error"]


def test_trigger_build_no_queue_url(mock_config, sample_jobs):
    """验证 Jenkins 未返回队列 URL 时标记为失败"""
    mock_config.get_jobs.return_value = sample_jobs[:1]

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.trigger_build.return_value = (None, None)
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["failed"]) == 1
        assert "Jenkins 未返回队列 URL" in result["failed"][0]["error"]


def test_trigger_build_exception(mock_config, sample_jobs):
    """验证 trigger_build 触发异常时返回错误"""
    mock_config.get_jobs.return_value = sample_jobs[:1]

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.trigger_build.side_effect = Exception("Connection refused")
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["failed"]) == 1
        assert "触发异常" in result["failed"][0]["error"]


def test_trigger_build_no_jobs_found(mock_config):
    """验证未找到项目时返回错误"""
    mock_config.get_jobs.return_value = []

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["triggered"]) == 0
        assert len(result["failed"]) == 1
        error = result["failed"][0]["error"]
        assert "没有匹配" in error or "没有找到" in error


# ============================================================================
# trigger_build 错误处理
# ============================================================================


def test_trigger_build_empty_env():
    """验证空环境名时返回错误"""
    from jenkins_config.mcp.tools.build_tools import trigger_build
    result = trigger_build(env="")

    assert len(result["triggered"]) == 0
    assert len(result["failed"]) == 1
    assert "必须指定环境名称" in result["failed"][0]["error"]


def test_trigger_build_config_not_found():
    """验证配置文件不存在时返回错误"""
    with patch(
        "jenkins_config.mcp.tools.build_tools.get_config",
        side_effect=FileNotFoundError("config.yaml 不存在"),
    ):
        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["triggered"]) == 0
        assert len(result["failed"]) == 1
        assert "配置文件不存在" in result["failed"][0]["error"]


def test_trigger_build_general_error():
    """验证一般错误时返回错误"""
    with patch(
        "jenkins_config.mcp.tools.build_tools.get_config",
        side_effect=Exception("未知错误"),
    ):
        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        assert len(result["failed"]) == 1
        assert "触发构建失败" in result["failed"][0]["error"]


# ============================================================================
# rebuild_last 测试
# ============================================================================


HISTORY_PATH_TARGET = "jenkins_config.mcp.tools.build_tools.resolve_history_path"


def _record(**overrides):
    """构造用于测试的 BuildRecord"""
    from jenkins_config.history import BuildRecord

    defaults = dict(
        timestamp="2026-08-24T10:00:00",
        env="dev",
        job_key="dev_project_a",
        build_num=100,
        status="SUCCESS",
        duration=60,
        log_file="",
    )
    defaults.update(overrides)
    return BuildRecord(**defaults)


def test_rebuild_last_config_mode_success(mock_config):
    """验证 rebuild_last 配置文件模式正常流程"""
    records = [_record(project_name="project-a", params={"branch": "develop"})]

    mock_job = Job(
        key="dev_project_a", path="project-a", branch="develop",
        params={"branch": "develop"}, env="dev",
    )
    mock_config.create_job_from_record.return_value = mock_job

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.history.HistoryManager") as MockHistory, \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch(HISTORY_PATH_TARGET, return_value=Path("/path/data/build_history.json")):

        mock_manager = Mock()
        mock_manager.get_last_build_group.return_value = records
        MockHistory.return_value = mock_manager

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 101
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import rebuild_last
        result = rebuild_last()

        assert len(result["triggered"]) == 1
        assert result["triggered"][0]["job_key"] == "dev_project_a"


def test_rebuild_last_no_history(mock_config):
    """验证 rebuild_last 无历史记录时返回错误"""
    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.history.HistoryManager") as MockHistory, \
         patch(HISTORY_PATH_TARGET, return_value=Path("/path/data/build_history.json")):

        mock_manager = Mock()
        mock_manager.get_last_build_group.return_value = []
        MockHistory.return_value = mock_manager

        from jenkins_config.mcp.tools.build_tools import rebuild_last
        result = rebuild_last()

        assert len(result["triggered"]) == 0
        assert len(result["failed"]) == 1
        assert "没有找到上次成功构建的记录" in result["failed"][0]["error"]


def test_rebuild_last_direct_mode_success(tmp_path):
    """验证 rebuild_last 直连模式正常流程（job_path 从 job_key 推导）"""
    records = [_record(params={"branch": "develop"})]

    hist_file = tmp_path / "build_history.json"
    hist_file.write_text('{"records": []}', encoding="utf-8")

    with patch("jenkins_config.history.HistoryManager") as MockHistory, \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_manager = Mock()
        mock_manager.get_last_build_group.return_value = records
        MockHistory.return_value = mock_manager

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 200
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import rebuild_last
        result = rebuild_last(
            jenkins_url="http://jenkins:8080",
            jenkins_token="test-token",
            history_file=str(hist_file),
        )

        assert len(result["triggered"]) == 1
        assert result["triggered"][0]["build_num"] == 200
        # job_key "dev_project_a" 去掉 "dev_" 前缀后 project_a -> project-a
        mock_client.trigger_build.assert_called_once()
        assert mock_client.trigger_build.call_args[0][0] == "project-a"


def test_rebuild_last_direct_mode_rejects_unlisted_host(monkeypatch, tmp_path):
    """验证直连模式的目标地址不在白名单时被拒绝"""
    monkeypatch.setenv(ALLOWED_HOSTS_ENV_VAR, "jenkins.internal")
    hist_file = tmp_path / "build_history.json"
    hist_file.write_text('{"records": []}', encoding="utf-8")

    with patch("jenkins_config.mcp.utils.trusted_server_url", return_value=""):
        from jenkins_config.mcp.tools.build_tools import rebuild_last
        result = rebuild_last(
            jenkins_url="http://evil.example.com",
            jenkins_token="test-token",
            history_file=str(hist_file),
        )

    assert result["triggered"] == []
    assert "不在允许范围内" in result["failed"][0]["error"]


def test_rebuild_last_rejects_half_specified_direct_mode(tmp_path):
    """验证只给 jenkins_url（漏传 token）时显式报错，不静默回落配置文件模式"""
    from jenkins_config.mcp.tools.build_tools import rebuild_last
    result = rebuild_last(jenkins_url="http://evil.example.com")

    assert result["triggered"] == []
    assert "必须同时提供" in result["failed"][0]["error"]


def test_rebuild_last_direct_mode_history_missing(tmp_path):

    """验证 rebuild_last 直连模式历史文件不存在时返回明确错误"""
    missing = tmp_path / "not_exist.json"

    from jenkins_config.mcp.tools.build_tools import rebuild_last
    result = rebuild_last(
        jenkins_url="http://jenkins:8080",
        jenkins_token="test-token",
        history_file=str(missing),
    )

    assert len(result["triggered"]) == 0
    assert "历史文件不存在" in result["failed"][0]["error"]


# ============================================================================
# 辅助函数测试
# ============================================================================


def test_parse_params_json():
    """验证 JSON 格式参数解析"""
    from jenkins_config.mcp.tools.build_tools import _parse_params_string

    parsed, skipped = _parse_params_string('{"BRANCH": "develop", "SKIP_TESTS": "true"}')
    assert parsed == {"BRANCH": "develop", "SKIP_TESTS": "true"}
    assert skipped == []


def test_parse_params_url_encoded():
    """验证 URL 编码格式参数解析（复用 config_io 的实现）"""
    from jenkins_config.mcp.tools.build_tools import _parse_params_string

    parsed, skipped = _parse_params_string("BRANCH=develop&SKIP_TESTS=true")
    assert parsed == {"BRANCH": "develop", "SKIP_TESTS": "true"}
    assert skipped == []


def test_parse_params_empty():
    """验证空字符串参数解析返回空字典"""
    from jenkins_config.mcp.tools.build_tools import _parse_params_string

    assert _parse_params_string("") == ({}, [])
    assert _parse_params_string("  ") == ({}, [])


def test_parse_params_invalid_json_raises():
    """验证以 { 开头的非法 JSON 直接抛错，不再静默回退"""
    from jenkins_config.mcp.tools.build_tools import _parse_params_string

    with pytest.raises(ValueError, match="不是合法的 JSON"):
        _parse_params_string("{invalid json}")


def test_parse_params_json_object_required():
    """验证 { 开头但不是对象的 JSON 被拒绝"""
    from jenkins_config.mcp.tools.build_tools import _parse_params_string

    with pytest.raises(ValueError, match="不是合法的 JSON"):
        _parse_params_string('{"a": 1')


def test_parse_params_json_non_string_values():
    """验证 JSON 中的非字符串值被转为字符串，布尔值转小写"""
    from jenkins_config.mcp.tools.build_tools import _parse_params_string

    parsed, skipped = _parse_params_string('{"SKIP_TESTS": true, "RETRY": false, "COUNT": 3}')
    assert parsed == {"SKIP_TESTS": "true", "RETRY": "false", "COUNT": "3"}
    assert skipped == []


def test_parse_params_skips_null_and_non_scalar():
    """验证 JSON 中的 null、列表、字典被跳过并在 skipped 中回传"""
    from jenkins_config.mcp.tools.build_tools import _parse_params_string

    parsed, skipped = _parse_params_string(
        '{"EMPTY": null, "LIST": [1, 2], "DICT": {"x": 1}, "OK": "ok", "NUM": 5}'
    )
    assert parsed == {"OK": "ok", "NUM": "5"}
    assert sorted(skipped) == ["DICT", "EMPTY", "LIST"]


# ============================================================================
# 修复验证：默认历史路径 / 探测超时 / BUILDING 占位 / 前缀守卫等
# ============================================================================


def test_rebuild_last_direct_mode_default_history_path(tmp_path):
    """验证直连模式不传 history_file 时默认锚定配置文件同级 data 目录"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("server: {}", encoding="utf-8")
    expected = tmp_path / "data" / "build_history.json"
    expected.parent.mkdir(parents=True)
    expected.write_text('{"records": []}', encoding="utf-8")

    with patch("jenkins_config.history.HistoryManager") as MockHistory, \
         patch("jenkins_config.jenkins.JenkinsClient"):

        mock_manager = Mock()
        mock_manager.get_last_build_group.return_value = []
        MockHistory.return_value = mock_manager

        from jenkins_config.mcp.tools.build_tools import rebuild_last
        result = rebuild_last(
            jenkins_url="http://jenkins:8080",
            jenkins_token="test-token",
            config_path=str(config_file),
        )

        assert len(result["triggered"]) == 0
        assert Path(MockHistory.call_args[0][0]) == expected


def test_trigger_direct_mode_probe_timeout_10s(tmp_path):
    """验证直连模式重建时构建编号探测超时为 10 秒"""
    records = [_record(params={"branch": "develop"})]

    hist_file = tmp_path / "build_history.json"
    hist_file.write_text('{"records": []}', encoding="utf-8")

    with patch("jenkins_config.history.HistoryManager") as MockHistory, \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_manager = Mock()
        mock_manager.get_last_build_group.return_value = records
        MockHistory.return_value = mock_manager

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 200
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import rebuild_last
        rebuild_last(
            jenkins_url="http://jenkins:8080",
            jenkins_token="test-token",
            history_file=str(hist_file),
        )

        assert mock_client.get_build_number.call_args.kwargs["timeout"] == 10


def test_trigger_config_mode_probe_timeout_capped_at_15(mock_config, sample_jobs):
    """验证配置文件模式探测超时为 min(queue_timeout, 15)"""
    mock_config.get_jobs.return_value = sample_jobs[:1]

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 123
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        trigger_build(env="dev")

        # BuildConfig 默认 queue_timeout=30，封顶 15 秒
        assert mock_client.get_build_number.call_args.kwargs["timeout"] == 15


def test_trigger_config_mode_probe_timeout_uses_small_queue_timeout(mock_config, sample_jobs):
    """验证 queue_timeout 小于 15 时探测超时取 queue_timeout"""
    mock_config.build.queue_timeout = 5
    mock_config.get_jobs.return_value = sample_jobs[:1]

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 123
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        trigger_build(env="dev")

        assert mock_client.get_build_number.call_args.kwargs["timeout"] == 5


def test_record_build_num_zero_when_number_not_assigned(tmp_path):
    """验证 get_build_number 返回 None 时记录以 build_num=0 落盘且字段正确"""
    from jenkins_config.history import HistoryManager

    hist_file = str(tmp_path / "build_history.json")
    job = Job(
        key="dev_project_a", path="project-a", branch="develop",
        params={"branch": "develop"}, env="dev", project_name="project-a",
    )

    client = Mock()
    client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
    client.get_build_number.return_value = None

    from jenkins_config.mcp.tools.build_tools import _trigger_jobs_with_client
    result = _trigger_jobs_with_client(client, [job], hist_file)

    assert len(result["triggered"]) == 1
    assert result["triggered"][0]["build_num"] is None

    # 验证落盘记录：存在且字段正确（build_num=0、status=BUILDING）
    records = HistoryManager(hist_file).list(limit=10)
    assert len(records) == 1
    record = records[0]
    assert record.build_num == 0
    assert record.status == "BUILDING"
    assert record.job_key == "dev_project_a"
    assert record.env == "dev"
    assert record.branch == "develop"
    assert record.project_name == "project-a"


def test_triggered_entries_contain_placeholder_note(mock_config, sample_jobs):
    """验证触发结果的每项均包含 BUILDING 占位记录说明"""
    mock_config.get_jobs.return_value = sample_jobs

    with patch("jenkins_config.mcp.tools.build_tools.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient, \
         patch("jenkins_config.history.HistoryManager"):

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 123
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import trigger_build
        result = trigger_build(env="dev")

        for entry in result["triggered"]:
            assert "note" in entry
            assert "get_build_status" in entry["note"]


def test_rebuild_last_direct_mode_skips_empty_env_and_prefix_mismatch(tmp_path):
    """验证 env 为空或前缀不匹配的记录进入 skipped 而非推导出错误路径"""
    records = [
        # env 为空的脏记录
        _record(env="", job_key="dev_project_a"),
        # job_key 不以 "{env}_" 开头的脏记录
        _record(env="dev", job_key="test_project_b", build_num=101),
    ]

    hist_file = tmp_path / "build_history.json"
    hist_file.write_text('{"records": []}', encoding="utf-8")

    with patch("jenkins_config.history.HistoryManager") as MockHistory, \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_manager = Mock()
        mock_manager.get_last_build_group.return_value = records
        MockHistory.return_value = mock_manager

        from jenkins_config.mcp.tools.build_tools import rebuild_last
        result = rebuild_last(
            jenkins_url="http://jenkins:8080",
            jenkins_token="test-token",
            history_file=str(hist_file),
        )

        assert len(result["triggered"]) == 0
        assert len(result["failed"]) == 2
        for item in result["failed"]:
            assert "无法从 job_key 推导 job_path" in item["error"]
        # 不应触发任何构建
        MockClient.return_value.trigger_build.assert_not_called()


def test_rebuild_last_direct_mode_writes_history(tmp_path):
    """验证直连模式重建后会将触发记录回写历史（与配置文件模式行为一致）"""
    records = [_record(params={"branch": "develop"})]

    hist_file = tmp_path / "build_history.json"
    hist_file.write_text('{"records": []}', encoding="utf-8")

    with patch("jenkins_config.history.HistoryManager") as MockHistory, \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_manager = Mock()
        mock_manager.get_last_build_group.return_value = records
        MockHistory.return_value = mock_manager

        mock_client = Mock()
        mock_client.trigger_build.return_value = ("http://jenkins/queue/item/1/", "")
        mock_client.get_build_number.return_value = 200
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.build_tools import rebuild_last
        result = rebuild_last(
            jenkins_url="http://jenkins:8080",
            jenkins_token="test-token",
            history_file=str(hist_file),
        )

        assert len(result["triggered"]) == 1
        # 验证历史回写：add_batch 被调用且记录的 env 来自历史记录
        mock_manager.add_batch.assert_called_once()
        written = mock_manager.add_batch.call_args[0][0]
        assert len(written) == 1
        assert written[0].env == "dev"
        assert written[0].job_key == "dev_project_a"
        assert written[0].build_num == 200
        # 直连模式也应带上 project_name，便于后续重建
        assert written[0].project_name == "project-a"


def test_record_triggered_uses_custom_branch_field(tmp_path):
    """验证 _record_triggered 通过 branch_field_for 按环境解析分支字段"""
    from jenkins_config.history import HistoryManager

    hist_file = str(tmp_path / "build_history.json")
    job = Job(
        key="dev_project_a", path="project-a", branch="feat/x",
        params={"BRANCH_NAME": "feat/x"}, env="dev", project_name="project-a",
    )

    from jenkins_config.mcp.tools.build_tools import _record_triggered
    _record_triggered([(job, 42)], hist_file, branch_field_for=lambda env: "BRANCH_NAME")

    records = HistoryManager(hist_file).list(limit=10)
    assert len(records) == 1
    assert records[0].branch == "feat/x"


def test_probe_build_numbers_survives_exception():
    """验证单个探测异常不影响整批结果，对应位置返回 None"""
    from jenkins_config.mcp.tools.build_tools import _probe_build_numbers

    client = Mock()
    client.get_build_number.side_effect = [Exception("boom"), 7]
    queued = [(Mock(), "http://q/1/"), (Mock(), "http://q/2/")]

    assert _probe_build_numbers(client, queued, 1) == [None, 7]
