# tests/test_jenkins.py
import pytest
from unittest.mock import Mock, patch
from jenkins_config.jenkins import JenkinsClient, BuildStatus


@pytest.fixture
def client():
    return JenkinsClient("http://localhost:8080", "test-token", "admin")


def _mock_crumb(mock_get):
    """配置 mock_get 返回 crumb"""
    mock_get.return_value.json.side_effect = [
        {"crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"},
    ]


def test_get_crumb(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb",
            "crumbRequestField": "Jenkins-Crumb",
        }
        result = client._get_crumb()
        assert result == ("Jenkins-Crumb", "test-crumb")


# ============================================================================
# trigger_build 基础测试（Git Parameter 相关测试见 test_jenkins_git_param.py）
# ============================================================================


def test_trigger_build_non_201(client):
    """触发构建返回非 201"""
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = set()
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
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
    with patch.object(client, "get_git_parameter_names") as mock_git_params, \
         patch.object(client.session, "get") as mock_get, \
         patch.object(client.session, "post") as mock_post:
        mock_git_params.return_value = set()
        mock_get.return_value.json.return_value = {
            "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
        }
        mock_post.side_effect = Exception("Connection refused")

        result, diagnostic = client.trigger_build("test-job", {"branch": "main"})
        assert result is None
        assert "Connection refused" in diagnostic


# ============================================================================
# get_build_number 测试
# ============================================================================


def test_get_build_number(client):
    with patch.object(client.session, "get") as mock_get, \
         patch("jenkins_config.jenkins.time.sleep"):
        # 第一次返回空，第二次返回编号
        mock_get.return_value.json.side_effect = [
            {"cancelled": False, "executable": None},
            {"cancelled": False, "executable": {"number": 456}},
        ]
        # timeout 需要足够覆盖多次轮询（轮询间隔 3 秒）
        result = client.get_build_number(
            "http://localhost:8080/queue/item/123/", timeout=10
        )
        assert result == 456


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


def test_get_build_number_exception(client):
    """队列查询异常时返回 None"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Queue API error")
        result = client.get_build_number("http://localhost/queue/item/1/", timeout=1)
        assert result is None


# ============================================================================
# get_build_status 测试
# ============================================================================


def test_get_build_status(client):
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.json.return_value = {
            "result": "SUCCESS",
            "duration": 60000,
        }
        info = client.get_build_status("test-job", 123)
        assert info.status == BuildStatus.SUCCESS
        assert info.duration == 60


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
    """查询状态 HTTP 错误时返回 UNKNOWN"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = False
        info = client.get_build_status("test-job", 999)
        assert info.status == BuildStatus.UNKNOWN


def test_get_build_status_exception(client):
    """状态查询异常时返回 UNKNOWN"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.side_effect = Exception("Status API error")
        info = client.get_build_status("test-job", 999)
        assert info.status == BuildStatus.UNKNOWN


# ============================================================================
# get_build_log 测试
# ============================================================================


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


# ============================================================================
# Crumb 测试
# ============================================================================


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
# 深度边缘路径（debug 日志）
# ============================================================================


def test_trigger_build_with_crumb_debug(client):
    """触发构建时带 CSRF Crumb，debug 日志行"""
    from jenkins_config.utils import set_debug_mode
    set_debug_mode(True)
    try:
        with patch.object(client, "get_git_parameter_names") as mock_git_params, \
             patch.object(client.session, "get") as mock_get, \
             patch.object(client.session, "post") as mock_post:
            mock_git_params.return_value = set()
            mock_get.return_value.json.return_value = {
                "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
            }
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


def test_trigger_build_success_with_crumb_and_debug(client):
    """触发构建成功且有 Crumb（debug 日志）"""
    from jenkins_config.utils import set_debug_mode
    set_debug_mode(True)
    try:
        with patch.object(client, "get_git_parameter_names") as mock_git_params, \
             patch.object(client.session, "get") as mock_get, \
             patch.object(client.session, "post") as mock_post:
            mock_git_params.return_value = set()
            mock_get.return_value.json.return_value = {
                "crumb": "test-crumb", "crumbRequestField": "Jenkins-Crumb"
            }
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
# 资源释放：close / 上下文管理器
# ============================================================================


def test_close_closes_session(client):
    """close() 应关闭底层 session"""
    with patch.object(client.session, "close") as mock_close:
        client.close()
        mock_close.assert_called_once()


def test_close_swallows_session_error(client):
    """session.close 抛错不应向上传播"""
    with patch.object(client.session, "close", side_effect=OSError("boom")):
        client.close()  # 不抛异常


def test_context_manager_closes_on_exit():
    """退出 with 块时自动调用 close()"""
    with patch.object(JenkinsClient, "close") as mock_close:
        with JenkinsClient("http://localhost:8080", "t", "admin") as c:
            assert isinstance(c, JenkinsClient)
        mock_close.assert_called_once()


def test_context_manager_does_not_suppress_exception():
    """__exit__ 返回 False，不吞上下文中的异常"""
    with patch.object(JenkinsClient, "close"):
        with pytest.raises(ValueError):
            with JenkinsClient("http://localhost:8080", "t", "admin"):
                raise ValueError("boom")


# ============================================================================
# get_build_log：max_bytes 尾部截断
# ============================================================================


def _stream_response(chunks, ok=True):
    """构造支持上下文管理器与 iter_content 的响应 mock"""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.ok = ok
    resp.iter_content.return_value = iter(chunks)
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def test_get_build_log_no_limit_uses_text(client):
    """未指定 max_bytes 时直接读取完整文本，不启用流式"""
    with patch.object(client.session, "get") as mock_get:
        mock_get.return_value.ok = True
        mock_get.return_value.text = "full log"
        assert client.get_build_log("job", 1) == "full log"
        assert mock_get.call_args.kwargs.get("stream") is None


def test_get_build_log_tail_truncates(client):
    """超过 max_bytes 时只保留尾部并带截断说明"""
    chunks = [b"a" * 100, b"b" * 100]
    with patch.object(client.session, "get", return_value=_stream_response(chunks)):
        log = client.get_build_log("job", 1, max_bytes=50)

    assert "日志已截断" in log
    assert "原始长度 200 字节" in log
    assert log.endswith("b" * 50)


def test_get_build_log_tail_no_truncation_when_small(client):
    """未超过 max_bytes 时不加截断说明"""
    with patch.object(client.session, "get", return_value=_stream_response([b"short"])):
        log = client.get_build_log("job", 1, max_bytes=1024)

    assert log == "short"


def test_get_build_log_tail_http_error(client):
    """流式获取日志遇到 HTTP 错误时返回空字符串"""
    with patch.object(
        client.session, "get", return_value=_stream_response([b"x"], ok=False)
    ):
        assert client.get_build_log("job", 1, max_bytes=1024) == ""


def test_get_build_log_tail_sends_suffix_range(client):
    """限制字节数时用 Range: bytes=-N 请求，只让服务端传输尾部"""
    with patch.object(
        client.session, "get", return_value=_stream_response([b"tail"])
    ) as mock_get:
        client.get_build_log("job", 1, max_bytes=2048)

    assert mock_get.call_args.kwargs["headers"]["Range"] == "bytes=-2048"
    assert mock_get.call_args.kwargs["stream"] is True


def test_get_build_log_tail_uses_content_range_total(client):
    """服务端返回 206 时原始长度取自 Content-Range，而非已下载字节数"""
    resp = _stream_response([b"b" * 50])
    resp.status_code = 206
    resp.headers = {"Content-Range": "bytes 950-999/1000"}

    with patch.object(client.session, "get", return_value=resp):
        log = client.get_build_log("job", 1, max_bytes=50)

    assert "原始长度 1000 字节" in log
    assert log.endswith("b" * 50)


def test_parse_content_range_total_handles_unknown_size():
    """Content-Range 总长度未知（*）或格式异常时返回 0"""
    from jenkins_config.jenkins import _parse_content_range_total

    assert _parse_content_range_total("bytes 0-49/1000") == 1000
    assert _parse_content_range_total("bytes 0-49/*") == 0
    assert _parse_content_range_total("") == 0


def test_redact_headers_masks_session_credentials():
    """调试日志中的 Set-Cookie / Authorization 必须脱敏"""
    from jenkins_config.jenkins import _redact_headers

    redacted = _redact_headers({
        "Set-Cookie": "JSESSIONID=abc; Path=/",
        "Authorization": "Basic dXNlcjp0b2tlbg==",
        "Location": "http://jenkins/queue/item/1/",
    })

    assert redacted["Set-Cookie"] == "***"
    assert redacted["Authorization"] == "***"
    assert redacted["Location"] == "http://jenkins/queue/item/1/"

