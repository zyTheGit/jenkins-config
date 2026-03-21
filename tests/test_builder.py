# tests/test_builder.py
import pytest
from unittest.mock import Mock, patch, MagicMock
from jenkins_config.builder import Builder, BuildResult
from jenkins_config.jenkins import BuildStatus, JenkinsClient
from jenkins_config.config import Config, Job, BuildConfig

@pytest.fixture
def mock_client():
    client = Mock(spec=JenkinsClient)
    client.trigger_build.return_value = "http://localhost/queue/item/1/"
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