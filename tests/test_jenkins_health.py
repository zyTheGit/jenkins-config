# tests/test_jenkins_health.py
"""health_check 的连接失败分类

代理故障（ProxyError 是 requests.ConnectionError 的子类）不得被改写成
"无法连接到 Jenkins 服务器"——服务器可达时那句话会把排查方向带偏。
"""
import pytest
import requests
from unittest.mock import patch

from jenkins_config.jenkins import JenkinsClient


@pytest.fixture
def client():
    with JenkinsClient("http://192.168.0.205:28090", "test-token") as c:
        yield c


def test_proxy_error_names_proxy_and_no_proxy_exit(client):
    """代理不可用时报出代理地址与 no_proxy 出口，并保留原始异常链"""
    err = requests.exceptions.ProxyError("Unable to connect to proxy")
    with patch.object(client.session, "get", side_effect=err), \
         patch("requests.utils.get_environ_proxies",
               return_value={"http": "http://127.0.0.1:7897"}):
        with pytest.raises(ConnectionError) as exc:
            client.health_check()

    msg = str(exc.value)
    assert "代理" in msg
    assert "127.0.0.1:7897" in msg
    assert "no_proxy" in msg
    assert "无法连接到 Jenkins 服务器" not in msg
    assert exc.value.__cause__ is err


def test_https_target_reports_https_proxy():
    """https 目标取 https 代理，不能误报成 http 代理"""
    with JenkinsClient("https://jenkins.example.com", "test-token") as client:
        with patch.object(client.session, "get",
                          side_effect=requests.exceptions.ProxyError("boom")), \
             patch("requests.utils.get_environ_proxies",
                   return_value={"http": "http://127.0.0.1:7897",
                                 "https": "http://127.0.0.1:7900"}):
            with pytest.raises(ConnectionError) as exc:
                client.health_check()

    assert "127.0.0.1:7900" in str(exc.value)
    assert "7897" not in str(exc.value)


def test_plain_connection_error_still_reports_unreachable(client):
    """非代理的连接失败仍报服务器不可达"""
    err = requests.exceptions.ConnectionError("Name or service not known")
    with patch.object(client.session, "get", side_effect=err):
        with pytest.raises(ConnectionError) as exc:
            client.health_check()

    assert str(exc.value) == "无法连接到 Jenkins 服务器: http://192.168.0.205:28090"
    assert exc.value.__cause__ is err


def _proxy_failure_message(url: str) -> str:
    """在指定 Jenkins 地址下触发代理故障，返回改写后的报错文案"""
    with JenkinsClient(url, "test-token") as client:
        with patch.object(client.session, "get",
                          side_effect=requests.exceptions.ProxyError("boom")), \
             patch("requests.utils.get_environ_proxies",
                   return_value={"http": "http://127.0.0.1:7897",
                                 "https": "http://127.0.0.1:7897"}):
            with pytest.raises(ConnectionError) as exc:
                client.health_check()
    return str(exc.value)


def test_no_proxy_hint_derives_host_from_config():
    """提示里的 no_proxy 条目取配置主机（去端口），不得写死网段"""
    msg = _proxy_failure_message("http://10.7.3.21:28090")

    assert "export no_proxy=$no_proxy,10.7.3.21" in msg
    assert "192.168.0.0/16" not in msg


def test_no_proxy_hint_supports_hostname():
    """主机是域名时同样给出可直接执行的提示"""
    msg = _proxy_failure_message("https://jenkins.internal.example.com")

    assert "export no_proxy=$no_proxy,jenkins.internal.example.com" in msg