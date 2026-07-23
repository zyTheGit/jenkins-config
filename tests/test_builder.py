# tests/test_builder.py
import time
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from jenkins_config.builder import Builder, BuildResult
from jenkins_config.config import Config, Job
from jenkins_config.config_types import BuildConfig
from jenkins_config.jenkins import BuildStatus, JenkinsClient


@pytest.fixture
def mock_client():
    client = Mock(spec=JenkinsClient)
    client.trigger_build.return_value = ("http://localhost/queue/item/1/", "")
    client.base_url = "http://localhost:8080"
    client.get_build_number.return_value = 123
    client.get_build_status.return_value = Mock(
        status=BuildStatus.SUCCESS,
        duration=60
    )
    client.get_build_log.return_value = "Build log content"
    return client

@pytest.fixture
def builder(mock_client):
    config = Mock(spec=Config)
    config.build = BuildConfig(build_timeout=60, poll_interval=1)
    return Builder(mock_client, config)

def test_build_single_success(builder, tmp_path):
    job = Job(key="dev_test", path="test-job", branch="main", params={}, env="dev")
    result = builder._build_single(job, str(tmp_path))

    assert result.status == BuildStatus.SUCCESS
    assert result.build_num == 123
    assert result.error is None

def test_build_sequential(builder, tmp_path):
    jobs = [
        Job(key="dev_a", path="job-a", branch="main", params={}, env="dev"),
        Job(key="dev_b", path="job-b", branch="main", params={}, env="dev")
    ]
    results = builder.build_sequential(jobs, str(tmp_path))

    assert len(results) == 2
    assert all(r.status == BuildStatus.SUCCESS for r in results)

def test_build_parallel(builder, tmp_path):
    jobs = [
        Job(key="dev_a", path="job-a", branch="main", params={}, env="dev"),
        Job(key="dev_b", path="job-b", branch="main", params={}, env="dev")
    ]
    results = builder.build_parallel(jobs, str(tmp_path))

    assert len(results) == 2


# ============================================================================
# 失败路径
# ============================================================================


def test_build_trigger_failure(builder, tmp_path):
    """触发失败时返回 FAILURE 状态"""
    builder.client.trigger_build.return_value = (None, "请求URL: http://localhost:8080/job/bad-job/buildWithParameters\n状态码: 404\n响应内容: Not Found")
    job = Job(key="dev_app", path="bad-job", branch="main", params={}, env="dev")

    result = builder._build_single(job, str(tmp_path))

    assert result.status == BuildStatus.FAILURE
    assert result.build_num == 0
    assert result.error == "触发构建失败"
    assert result.log_file.endswith("_error.log")


def test_build_queue_timeout(builder, tmp_path):
    """队列超时返回 TIMEOUT 状态"""
    builder.client.get_build_number.return_value = None
    job = Job(key="dev_app", path="slow-job", branch="main", params={}, env="dev")

    result = builder._build_single(job, str(tmp_path))

    assert result.status == BuildStatus.TIMEOUT
    assert result.build_num == 0
    assert result.error == "获取构建编号超时"


def test_build_queue_timeout_uses_config(builder, tmp_path):
    """队列等待超时应取自 build.queue_timeout 配置，而非硬编码 30 秒"""
    builder.config.build = BuildConfig(
        queue_timeout=120, build_timeout=60, poll_interval=1
    )
    builder.client.get_build_number.return_value = None
    job = Job(key="dev_app", path="slow-job", branch="main", params={}, env="dev")

    result = builder._build_single(job, str(tmp_path))

    # 配置的超时值应透传给 get_build_number
    assert builder.client.get_build_number.call_args.kwargs["timeout"] == 120
    assert result.status == BuildStatus.TIMEOUT
    # 错误日志中的超时秒数也应为配置值
    content = Path(result.log_file).read_text(encoding="utf-8")
    assert "120秒" in content



def test_build_aborted_from_status(builder, tmp_path):
    """构建被中止"""
    builder.client.get_build_status.return_value = Mock(
        status=BuildStatus.ABORTED, duration=10
    )
    builder.client.get_build_log.return_value = "Aborted by user"
    job = Job(key="dev_app", path="aborted-job", branch="main", params={}, env="dev")

    result = builder._build_single(job, str(tmp_path))

    assert result.status == BuildStatus.ABORTED
    assert result.build_num == 123


# ============================================================================
# 深度失败路径
# ============================================================================


def test_build_empty_log_for_failure(builder, tmp_path):
    """构建失败时日志为空，生成诊断信息"""
    builder.client.get_build_log.return_value = ""
    builder.client.get_build_status.return_value = Mock(
        status=BuildStatus.FAILURE, duration=10
    )
    builder.client.base_url = "http://jenkins:8080"
    job = Job(key="dev_app", path="fail-job", branch="main", params={}, env="dev")

    result = builder._build_single(job, str(tmp_path))

    assert result.status == BuildStatus.FAILURE
    assert result.build_num == 123
    # 验证日志文件包含诊断信息
    log_path = result.log_file  # type: ignore
    content = Path(log_path).read_text(encoding="utf-8")
    assert "构建日志获取失败或为空" in content
    assert "可能原因" in content
    assert "Jenkins 控制台" in content


def test_build_failure_with_error_lines(builder, tmp_path):
    """构建失败且日志包含错误关键词，提取错误行"""
    error_log = (
        "some output\n"
        "make: *** [build] Error 1\n"
        "npm ERR! code ELIFECYCLE\n"
        "normal line\n"
    )
    builder.client.get_build_log.return_value = error_log
    builder.client.get_build_status.return_value = Mock(
        status=BuildStatus.FAILURE, duration=10
    )
    job = Job(key="dev_app", path="fail-job", branch="main", params={}, env="dev")

    result = builder._build_single(job, str(tmp_path))

    assert result.status == BuildStatus.FAILURE
    log_path = Path(result.log_file)
    saved_log = log_path.read_text(encoding="utf-8")
    assert "make:" in saved_log
    assert "npm ERR!" in saved_log
    assert "normal line" in saved_log


def test_build_intermediate_building(builder, tmp_path):
    """构建过程中间状态 BUILDING"""
    status_building = Mock(status=BuildStatus.BUILDING, duration=10)
    status_success = Mock(status=BuildStatus.SUCCESS, duration=30)
    # _build_single 在 _wait_for_build 之后还会调一次 get_build_status 取 duration
    builder.client.get_build_status.side_effect = [
        status_building, status_success, status_success,
    ]
    builder.client.get_build_log.return_value = "build output"
    job = Job(key="dev_app", path="build-job", branch="main", params={}, env="dev")

    with patch.object(time, "sleep"):  # 加速测试
        result = builder._build_single(job, str(tmp_path))

    assert result.status == BuildStatus.SUCCESS
    assert result.duration == 30


@pytest.fixture
def builder_short_timeout():
    """带短超时的构建器"""
    client = Mock(spec=JenkinsClient)
    client.trigger_build.return_value = ("http://localhost/queue/item/1/", "")
    client.base_url = "http://localhost:8080"
    client.get_build_number.return_value = 99
    config = Mock(spec=Config)
    config.build = BuildConfig(build_timeout=1, poll_interval=3600)
    return Builder(client, config)


def test_wait_for_build_timeout(builder_short_timeout):
    """_wait_for_build 超时返回 TIMEOUT"""
    builder_short_timeout.client.get_build_status.return_value = Mock(
        status=BuildStatus.BUILDING, duration=0
    )
    job = Job(key="dev_app", path="timeout-job", branch="main", params={}, env="dev")

    with patch("jenkins_config.builder.time.time", side_effect=[0, 2]):
        status = builder_short_timeout._wait_for_build(job, 99)

    assert status == BuildStatus.TIMEOUT
