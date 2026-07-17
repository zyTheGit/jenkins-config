# tests/test_jenkins_git_param.py
"""
Git Parameter 自动前缀功能测试

测试 JenkinsClient.get_git_parameter_names() 和
trigger_build 对 GitParameterDefinition 参数自动添加 origin/ 前缀的逻辑。
"""
import pytest
from unittest.mock import Mock, patch
from jenkins_config.jenkins import JenkinsClient


@pytest.fixture
def client():
    return JenkinsClient("http://localhost:8080", "test-token", "admin")


# ============================================================================
# get_git_parameter_names 测试
# ============================================================================


def test_get_git_parameter_names_success(client):
    """查询 Git Parameter 参数名成功"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {
            "property": [
                {
                    "parameterDefinitions": [
                        {
                            "name": "BRANCH",
                            "_class": "net.uaznia.lukanus.hudson.plugins.gitparameter.GitParameterDefinition",
                        },
                        {
                            "name": "TAG",
                            "_class": "net.uaznia.lukanus.hudson.plugins.gitparameter.GitParameterDefinition",
                        },
                    ]
                }
            ]
        }
        result = client.get_git_parameter_names("test-job")
        assert result == {"BRANCH", "TAG"}


def test_get_git_parameter_names_no_git_params(client):
    """Job 没有 Git Parameter 参数"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {
            "property": [
                {
                    "parameterDefinitions": [
                        {"name": "BRANCH", "_class": "hudson.model.StringParameterDefinition"},
                    ]
                }
            ]
        }
        result = client.get_git_parameter_names("test-job")
        assert result == set()


def test_get_git_parameter_names_http_error(client):
    """查询参数定义 HTTP 错误，不缓存失败结果"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = False
        result = client.get_git_parameter_names("test-job")
        assert result == set()
        # 失败结果不应缓存，下次调用应重试
        assert "test-job" not in client._git_param_cache


def test_get_git_parameter_names_exception(client):
    """查询参数定义异常，不缓存失败结果"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Network error")
        result = client.get_git_parameter_names("test-job")
        assert result == set()
        # 异常结果不应缓存
        assert "test-job" not in client._git_param_cache


def test_get_git_parameter_names_cached(client):
    """缓存：成功查询后第二次不发送请求"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {
            "property": [
                {
                    "parameterDefinitions": [
                        {"name": "BRANCH", "_class": "net.uaznia.lukanus.hudson.plugins.gitparameter.GitParameterDefinition"},
                    ]
                }
            ]
        }
        # 第一次查询
        result1 = client.get_git_parameter_names("test-job")
        assert result1 == {"BRANCH"}
        call_count_1 = mock_get.call_count

        # 第二次查询（应命中缓存）
        result2 = client.get_git_parameter_names("test-job")
        assert result2 == {"BRANCH"}
        assert mock_get.call_count == call_count_1  # 无额外请求


def test_get_git_parameter_names_empty_params_cached(client):
    """成功查询但无 Git Parameter 参数，缓存空集合"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {
            "property": [
                {
                    "parameterDefinitions": [
                        {"name": "BRANCH", "_class": "hudson.model.StringParameterDefinition"},
                    ]
                }
            ]
        }
        result1 = client.get_git_parameter_names("test-job")
        assert result1 == set()
        call_count_1 = mock_get.call_count

        # 空集合应缓存（成功查询，只是没有 Git Parameter）
        result2 = client.get_git_parameter_names("test-job")
        assert result2 == set()
        assert mock_get.call_count == call_count_1


# ============================================================================
# trigger_build Git Parameter 自动前缀测试
# ============================================================================


def test_trigger_build_git_param_auto_prefix(client):
    """Git Parameter 参数值自动添加 origin/ 前缀"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = {"BRANCH"}
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("test-job", {"BRANCH": "prod"})
        assert result == "http://localhost:8080/queue/item/123/"
        assert diagnostic == ""
        # BRANCH="prod" 应自动变为 "origin/prod"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "origin/prod"


def test_trigger_build_git_param_already_has_prefix(client):
    """Git Parameter 参数值已有 origin/ 前缀，不重复添加"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = {"BRANCH"}
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("test-job", {"BRANCH": "origin/prod"})
        assert result == "http://localhost:8080/queue/item/123/"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "origin/prod"  # 不变


def test_trigger_build_non_git_param_unchanged(client):
    """非 Git Parameter 参数值不变"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = {"BRANCH"}
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        params = {"BRANCH": "prod", "SKIP_TESTS": "true", "VERSION": "1.0"}
        result, diagnostic = client.trigger_build("test-job", params)
        assert result == "http://localhost:8080/queue/item/123/"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "origin/prod"  # Git Parameter，自动加前缀
        assert call_params["SKIP_TESTS"] == "true"      # 非 Git Parameter，不变
        assert call_params["VERSION"] == "1.0"           # 非 Git Parameter，不变


def test_trigger_build_no_git_params(client):
    """Job 没有 Git Parameter 参数，所有参数原样发送"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = set()  # 无 Git Parameter
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("test-job", {"BRANCH": "prod"})
        assert result == "http://localhost:8080/queue/item/123/"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "prod"  # 无 Git Parameter，原样发送


def test_trigger_build_git_param_empty_value(client):
    """Git Parameter 参数值为空字符串，不添加前缀"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = {"BRANCH"}
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        result, diagnostic = client.trigger_build("test-job", {"BRANCH": ""})
        assert result == "http://localhost:8080/queue/item/123/"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == ""  # 空值不加前缀


def test_trigger_build_original_params_unchanged(client):
    """trigger_build 不修改原始 params 字典"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = {"BRANCH"}
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        original_params = {"BRANCH": "prod"}
        result, diagnostic = client.trigger_build("test-job", original_params)
        assert result == "http://localhost:8080/queue/item/123/"
        # 原始字典不应被修改
        assert original_params["BRANCH"] == "prod"


def test_trigger_build_multiple_git_params(client):
    """多个 Git Parameter 参数都自动加前缀"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = {"BRANCH", "TAG"}
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_response = Mock()
        mock_response.status_code = 201
        mock_response.headers = {"Location": "http://localhost:8080/queue/item/123/"}
        mock_post.return_value = mock_response

        params = {"BRANCH": "develop", "TAG": "v1.0"}
        result, diagnostic = client.trigger_build("test-job", params)
        assert result == "http://localhost:8080/queue/item/123/"
        call_params = mock_post.call_args[1]["data"]
        assert call_params["BRANCH"] == "origin/develop"
        assert call_params["TAG"] == "origin/v1.0"
