# tests/test_jenkins.py
import pytest
from unittest.mock import Mock, patch, MagicMock
from jenkins_config.jenkins import JenkinsClient, BuildStatus


@pytest.fixture
def client():
    return JenkinsClient("http://localhost:8080", "test-token", "admin")


def _mock_freestyle(mock_get):
    """配置 mock_get 返回 FreeStyleProject 类型"""
    # _is_pipeline_job 调用 session.get，需要区分 crumb 和 job type 查询
    # 简化：让所有 session.get 返回 FreeStyleProject
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = {
        "_class": "hudson.model.FreeStyleProject"
    }


def _mock_pipeline(mock_get):
    """配置 mock_get 返回 WorkflowJob 类型"""
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = {
        "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob"
    }


def test_get_crumb(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb",
            "crumbRequestField": "Jenkins-Crumb",
        }
        result = client._get_crumb()
        assert result == ("Jenkins-Crumb", "test-crumb")


def test_trigger_build_success(client):
    """FreeStyle Job 触发成功，参数不变"""
    with patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        _mock_freestyle(mock_get)
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("test-job", {"BRANCH": "origin/prod"})
        assert result == "http://localhost:8080/queue/item/123/"
        assert diagnostic == ""
        # FreeStyle 不去前缀，参数原样发送
        call_params = mock_post.call_args[1].get("data") or mock_post.call_args[0][1] if mock_post.call_args[0] else mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "origin/prod"


def test_trigger_build_pipeline_strips_origin(client):
    """Pipeline Job 自动去掉 origin/ 前缀"""
    with patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        _mock_pipeline(mock_get)
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/456/"}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("pipeline-job", {"BRANCH": "origin/prod"})
        assert result == "http://localhost:8080/queue/item/456/"
        assert diagnostic == ""
        # Pipeline 去掉 origin/ 前缀
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "prod"


def test_trigger_build_pipeline_no_origin_prefix(client):
    """Pipeline Job 参数无 origin/ 前缀时不修改"""
    with patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        _mock_pipeline(mock_get)
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/789/"}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("pipeline-job", {"BRANCH": "develop"})
        assert result == "http://localhost:8080/queue/item/789/"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "develop"


def test_trigger_build_pipeline_mixed_params(client):
    """Pipeline Job 只有含 origin/ 的参数被修改，其他参数不变"""
    with patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        _mock_pipeline(mock_get)
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/100/"}
        mock_post.return_value = mock_response

        params = {"BRANCH": "origin/prod", "SKIP_TESTS": "true", "VERSION": "1.0"}
        result, diagnostic = client.trigger_build("pipeline-job", params)
        assert result == "http://localhost:8080/queue/item/100/"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "prod"
        assert call_params["SKIP_TESTS"] == "true"
        assert call_params["VERSION"] == "1.0"


def test_get_build_number(client):
    with patch.object(client.session, "get") as mock_get:
        # 第一次返回空，第二次返回编号
        mock_get.return_value.json.side_effect = [
            {"cancelled": False, "executable": None},
            {"cancelled": False, "executable": {"number": 456}},
        ]
        result = client.get_build_number(
            "http://localhost:8080/queue/item/123/", timeout=2
        )
        assert result == 456


def test_get_build_status(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": "SUCCESS",
            "duration": 60000,
        }
        info = client.get_build_status("test-job", 123)
        assert info.status == BuildStatus.SUCCESS
        assert info.duration == 60


# ============================================================================
# 失败路径
# ============================================================================


def test_trigger_build_non_201(client):
    """触发构建返回非 201"""
    with patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        _mock_freestyle(mock_get)
        mock_response = Mock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_response.headers = {}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("test-job", {"branch": "main"})
        assert result is None
        assert "403" in diagnostic


def test_trigger_build_network_error(client):
    """触发构建网络异常"""
    with patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        _mock_freestyle(mock_get)
        mock_post.side_effect = Exception("Connection refused")

        result, diagnostic = client.trigger_build("test-job", {"branch": "main"})
        assert result is None
        assert "Connection refused" in diagnostic


def test_get_build_number_cancelled(client):
    """队列中的构建被取消"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cancelled": True,
            "executable": None,
        }
        result = client.get_build_number(
            "http://localhost/queue/item/1/", timeout=1
        )
        assert result is None


def test_get_build_number_timeout(client):
    """获取构建编号超时"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "cancelled": False,
            "executable": None,
        }
        result = client.get_build_number(
            "http://localhost/queue/item/1/", timeout=1
        )
        assert result is None


def test_get_build_number_http_error(client):
    """队列 API 返回错误"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = False
        result = client.get_build_number(
            "http://localhost/queue/item/1/", timeout=1
        )
        assert result is None


def test_get_build_status_failure(client):
    """构建失败"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": "FAILURE",
            "duration": 30000,
        }
        info = client.get_build_status("test-job", 456)
        assert info.status == BuildStatus.FAILURE
        assert info.duration == 30


def test_get_build_status_aborted(client):
    """构建被中止"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": "ABORTED",
            "duration": 10000,
        }
        info = client.get_build_status("test-job", 789)
        assert info.status == BuildStatus.ABORTED


def test_get_build_status_building(client):
    """构建中（result 为 null）"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": None,
            "duration": 0,
        }
        info = client.get_build_status("test-job", 101)
        assert info.status == BuildStatus.BUILDING


def test_get_build_status_http_error(client):
    """查询状态 HTTP 错误时返回 BUILDING"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = False
        info = client.get_build_status("test-job", 999)
        assert info.status == BuildStatus.BUILDING


def test_get_build_log_empty(client):
    """获取日志返回空"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.text = ""
        result = client.get_build_log("test-job", 123)
        assert result == ""


def test_get_build_log_http_error(client):
    """获取日志 HTTP 错误"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = False
        result = client.get_build_log("test-job", 123)
        assert result == ""


def test_get_build_log_network_error(client):
    """获取日志网络异常"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Connection error")
        result = client.get_build_log("test-job", 123)
        assert result == ""


def test_get_crumb_failure(client):
    """获取 Crumb 失败"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = False
        crumb = client._get_crumb()
        assert crumb is None


def test_get_crumb_exception(client):
    """获取 Crumb 异常"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Network error")
        crumb = client._get_crumb()
        assert crumb is None


# ============================================================================
# 深度边缘路径（异常处理、debug 日志）
# ============================================================================


def test_trigger_build_with_crumb_debug(client):
    """触发构建时带 CSRF Crumb，debug 日志行"""
    from jenkins_config.utils import set_debug_mode
    set_debug_mode(True)
    try:
        with patch.object(client.session, "get") as mock_get, \
             patch.object(client.session, "post") as mock_post:
            _mock_freestyle(mock_get)
            mock_response = Mock()
            mock_response.status_code = 403
            mock_response.text = "Forbidden"
            mock_response.headers = {}
            mock_post.return_value = mock_response

            result, diagnostic = client.trigger_build("test-job", {"branch": "main"})
            assert result is None  # 403 触发 debug 日志
            assert "403" in diagnostic
    finally:
        set_debug_mode(False)


def test_get_build_number_exception(client):
    """队列查询异常时返回 None"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Queue API error")
        result = client.get_build_number("http://localhost/queue/item/1/", timeout=1)
        assert result is None


def test_get_build_status_exception(client):
    """状态查询异常时返回 BUILDING"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Status API error")
        info = client.get_build_status("test-job", 999)
        assert info.status == BuildStatus.BUILDING


def test_trigger_build_success_with_crumb_and_debug(client):
    """触发构建成功且有 Crumb（debug 日志）"""
    from jenkins_config.utils import set_debug_mode
    set_debug_mode(True)
    try:
        with patch.object(client.session, "get") as mock_get, \
             patch.object(client.session, "post") as mock_post:
            _mock_freestyle(mock_get)
            mock_response = Mock()
            mock_response.status_code = 201
            mock_response.headers = {
                "Location": "http://localhost:8080/queue/item/1/"
            }
            mock_post.return_value = mock_response

            result, diagnostic = client.trigger_build("test-job", {"branch": "main"})
            assert result == "http://localhost:8080/queue/item/1/"
            assert diagnostic == ""
    finally:
        set_debug_mode(False)


# ============================================================================
# _is_pipeline_job 检测
# ============================================================================


def test_is_pipeline_job_true(client):
    """Pipeline Job 检测返回 True"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {
            "_class": "org.jenkinsci.plugins.workflow.job.WorkflowJob"
        }
        assert client._is_pipeline_job("pipeline-job") is True


def test_is_pipeline_job_false(client):
    """FreeStyle Job 检测返回 False"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {
            "_class": "hudson.model.FreeStyleProject"
        }
        assert client._is_pipeline_job("freestyle-job") is False


def test_is_pipeline_job_api_error(client):
    """Job 类型查询失败时返回 False（保守策略：保留原始参数）"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = False
        assert client._is_pipeline_job("unknown-job") is False


def test_is_pipeline_job_exception(client):
    """Job 类型查询异常时返回 False"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Network error")
        assert client._is_pipeline_job("unknown-job") is False
