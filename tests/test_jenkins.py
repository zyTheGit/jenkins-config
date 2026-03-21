# tests/test_jenkins.py
import pytest
from unittest.mock import Mock, patch, MagicMock
from jenkins_config.jenkins import JenkinsClient, BuildStatus

@pytest.fixture
def client():
    return JenkinsClient("http://localhost:8080", "test-token")

def test_get_crumb(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb",
            "crumbRequestField": "Jenkins-Crumb"
        }
        result = client._get_crumb()
        assert result == ("Jenkins-Crumb", "test-crumb")

def test_trigger_build_success(client):
    with patch.object(client.session, "post") as mock_post:
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response
        
        result = client.trigger_build("test-job", {"branch": "main"})
        assert result == "http://localhost:8080/queue/item/123/"

def test_get_build_number(client):
    with patch.object(client.session, "get") as mock_get:
        # 第一次返回空，第二次返回编号
        mock_get.return_value.json.side_effect = [
            {"cancelled": False, "executable": None},
            {"cancelled": False, "executable": {"number": 456}}
        ]
        result = client.get_build_number("http://localhost:8080/queue/item/123/", timeout=2)
        assert result == 456

def test_get_build_status(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": "SUCCESS",
            "duration": 60000
        }
        info = client.get_build_status("test-job", 123)
        assert info.status == BuildStatus.SUCCESS
        assert info.duration == 60