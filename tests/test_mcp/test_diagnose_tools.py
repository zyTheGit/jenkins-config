# tests/test_mcp/test_diagnose_tools.py
"""
MCP 诊断查询工具测试

测试覆盖：
- health_check 成功 / 连接失败 / 配置加载失败
- get_build_status 成功 / 异常
- get_build_log 成功 / 截断 / 空日志 / 异常
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from jenkins_config.config_types import BuildConfig, ServerConfig
from jenkins_config.jenkins import BuildStatus


# ============================================================================
# Fixtures
# ============================================================================


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
    return config


# ============================================================================
# health_check 测试
# ============================================================================


def test_health_check_success(mock_config):
    """验证 health_check 可达时返回 reachable 和 url"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.health_check.return_value = True
        mock_client.base_url = "http://jenkins:8080"
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import health_check
        result = health_check()

        assert result["reachable"] is True
        assert result["url"] == "http://jenkins:8080"
        assert "error" not in result


def test_health_check_connection_error(mock_config):
    """验证 health_check 连接失败时返回错误信息"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.health_check.side_effect = ConnectionError("无法连接到 Jenkins 服务器")
        mock_client.base_url = "http://jenkins:8080"
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import health_check
        result = health_check()

        assert result["reachable"] is False
        assert result["url"] == "http://jenkins:8080"
        assert "连接失败" in result["error"]


def test_health_check_config_load_error():
    """验证配置文件加载失败时返回错误信息"""
    with patch("jenkins_config.mcp.utils.get_config", side_effect=FileNotFoundError("配置文件不存在")):
        from jenkins_config.mcp.tools.diagnose_tools import health_check
        result = health_check()

        assert result["reachable"] is False
        assert result["url"] == ""
        assert "健康检查失败" in result["error"]


# ============================================================================
# get_build_status 测试
# ============================================================================


def test_get_build_status_success(mock_config):
    """验证 get_build_status 返回正确的状态信息"""
    info = SimpleNamespace(
        number=100,
        status=BuildStatus.SUCCESS,
        result="SUCCESS",
        duration=65,
    )

    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_status.return_value = info
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import get_build_status
        result = get_build_status("project-a", 100)

        mock_client.get_build_status.assert_called_once_with("project-a", 100)
        assert result["number"] == 100
        assert result["status"] == "SUCCESS"
        assert result["result"] == "SUCCESS"
        assert result["duration"] == "1m 5s"


def test_get_build_status_short_duration(mock_config):
    """验证耗时不足 60 秒时的格式化"""
    info = SimpleNamespace(
        number=101,
        status=BuildStatus.BUILDING,
        result=None,
        duration=45,
    )

    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_status.return_value = info
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import get_build_status
        result = get_build_status("project-a", 101)

        assert result["status"] == "BUILDING"
        assert result["result"] is None
        assert result["duration"] == "45s"


def test_get_build_status_error(mock_config):
    """验证查询异常时返回 UNKNOWN 状态"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_status.side_effect = Exception("HTTP 404")
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import get_build_status
        result = get_build_status("project-a", 999)

        assert result["number"] == 999
        assert result["status"] == "UNKNOWN"
        assert result["result"] is None
        assert "查询构建状态失败" in result["error"]


# ============================================================================
# get_build_log 测试
# ============================================================================


def test_get_build_log_success(mock_config):
    """验证 get_build_log 返回日志文本，并默认按尾部截断请求"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_log.return_value = "Started by user admin\nFinished: SUCCESS"
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import DEFAULT_LOG_TAIL_KB, get_build_log
        result = get_build_log("project-a", 100)

        mock_client.get_build_log.assert_called_once_with(
            "project-a", 100, max_bytes=DEFAULT_LOG_TAIL_KB * 1024
        )
        assert "Finished: SUCCESS" in result


def test_get_build_log_full_when_tail_kb_non_positive(mock_config):
    """验证 tail_kb<=0 时请求全量日志（max_bytes=None）"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_log.return_value = "full log"
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import get_build_log
        get_build_log("project-a", 100, tail_kb=0)

        mock_client.get_build_log.assert_called_once_with("project-a", 100, max_bytes=None)


def test_get_build_log_closes_client(mock_config):
    """验证工具调用结束后关闭底层客户端，避免连接泄漏"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_log.return_value = "log"
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import get_build_log
        get_build_log("project-a", 100)

        mock_client.close.assert_called_once()


def test_get_build_log_empty(mock_config):
    """验证未获取到日志时返回友好提示"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_log.return_value = ""
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import get_build_log
        result = get_build_log("project-a", 100)

        assert "未获取到" in result


def test_get_build_log_error(mock_config):
    """验证异常时返回错误信息"""
    with patch("jenkins_config.mcp.utils.get_config", return_value=mock_config), \
         patch("jenkins_config.jenkins.JenkinsClient") as MockClient:

        mock_client = Mock()
        mock_client.get_build_log.side_effect = Exception("Connection refused")
        MockClient.return_value = mock_client

        from jenkins_config.mcp.tools.diagnose_tools import get_build_log
        result = get_build_log("project-a", 100)

        assert "获取构建日志失败" in result
