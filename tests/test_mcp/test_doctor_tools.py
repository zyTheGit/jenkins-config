# tests/test_mcp/test_doctor_tools.py
"""
doctor（本地体检）测试

覆盖：
- 正常配置 → status=ok、11 项齐全、jenkins_reachable=skip
- 配置不存在 → config_located=error 且 next_steps 指向 init_config
- 非法 YAML → config_located=ok 且 config_parsable=error
- 只读模式（仅 warn）不把整体拉到 error
- 返回体 JSON 全文不含 token 原文
"""

import json

import pytest

from jenkins_config.mcp.tools.doctor_tools import doctor
from jenkins_config.mcp.utils import (
    ALLOWED_HOSTS_ENV_VAR,
    CONFIG_ENV_VAR,
    WRITE_ENV_VAR,
)

# 固定 11 项检查，顺序即体检的排查顺序
EXPECTED_CHECKS = [
    "config_located",
    "config_readable",
    "config_parsable",
    "config_complete",
    "config_path_allowed",
    "write_gate",
    "allowed_hosts",
    "history_path",
    "log_sink",
    "runtime_mode",
    "jenkins_reachable",
]

TOKEN = "s3cr3t-token-value"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """清掉可能影响体检结论的环境变量（conftest 只注入配置根白名单）"""
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)
    monkeypatch.delenv(ALLOWED_HOSTS_ENV_VAR, raising=False)


@pytest.fixture
def good_config(tmp_path):
    """写一份填写完整的配置文件并返回其路径"""
    config = tmp_path / "jenkins-config.yaml"
    config.write_text(
        "server:\n"
        "  url: http://jenkins.example.com\n"
        "  username: admin\n"
        f"  token: {TOKEN}\n"
        "environments:\n"
        "  dev:\n"
        "    description: 开发环境\n"
        "    projects:\n"
        "      - name: project-a\n",
        encoding="utf-8",
    )
    return config


def _by_name(result):
    """把 checks 列表转成 name -> check 的字典"""
    return {item["name"]: item for item in result["checks"]}


def test_doctor_all_ok(good_config, monkeypatch):
    """验证配置完整且两个环境变量都设好时整体 ok，11 项齐全"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")
    monkeypatch.setenv(ALLOWED_HOSTS_ENV_VAR, "jenkins.example.com")

    result = doctor(str(good_config))

    assert [item["name"] for item in result["checks"]] == EXPECTED_CHECKS
    assert result["status"] == "ok"
    assert result["summary"]["error"] == 0
    assert result["next_steps"] == []
    assert _by_name(result)["jenkins_reachable"]["status"] == "skip"
    assert result["config_path"] == str(good_config)


def test_doctor_does_not_touch_network_by_default(good_config, monkeypatch):
    """验证默认不发起 Jenkins 请求：连客户端都不构造"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")

    def _boom(*args, **kwargs):
        raise AssertionError("doctor 默认不应创建 Jenkins 客户端")

    monkeypatch.setattr("jenkins_config.mcp.tools.doctor_tools.jenkins_client", _boom)

    assert doctor(str(good_config))["checks"]


def test_doctor_warn_only_does_not_escalate_to_error(good_config):
    """验证只读模式 + 未设主机白名单只判 warn，整体不升为 error"""
    result = doctor(str(good_config))
    checks = _by_name(result)

    assert checks["write_gate"]["status"] == "warn"
    assert checks["allowed_hosts"]["status"] == "warn"
    assert result["status"] == "warn"
    assert result["summary"]["error"] == 0
    # status != ok 时必须给出可执行的下一步
    assert result["next_steps"]


def test_doctor_config_missing(tmp_path):
    """验证配置文件不存在时 config_located=error 且建议指向 init_config"""
    missing = tmp_path / "nope" / "jenkins-config.yaml"

    result = doctor(str(missing))
    checks = _by_name(result)

    assert checks["config_located"]["status"] == "error"
    assert result["status"] == "error"
    assert "init_config" in checks["config_located"]["hint"]
    assert any("init_config" in step for step in result["next_steps"])
    # 下游三项短路为 skip，避免同一根因报三次
    for name in ("config_readable", "config_parsable", "config_complete"):
        assert checks[name]["status"] == "skip"


def test_doctor_invalid_yaml(tmp_path):
    """验证非法 YAML 时定位成功但解析失败"""
    broken = tmp_path / "jenkins-config.yaml"
    broken.write_text("server:\n  url: [http://x\n", encoding="utf-8")

    result = doctor(str(broken))
    checks = _by_name(result)

    assert checks["config_located"]["status"] == "ok"
    assert checks["config_readable"]["status"] == "ok"
    assert checks["config_parsable"]["status"] == "error"
    assert str(broken) in checks["config_parsable"]["detail"]
    assert result["status"] == "error"


def test_doctor_placeholder_config_warns(tmp_path):
    """验证只 init 过没填过的配置判 warn，并报出占位符字段名

    模板占位符非空，能通过 _validate_config，所以这类"没填"只能靠占位符比对发现。
    """
    from jenkins_config.config_io import PLACEHOLDER_VALUES, generate_template

    import yaml

    template = tmp_path / "jenkins-config.yaml"
    template.write_text(
        yaml.safe_dump(generate_template(), allow_unicode=True), encoding="utf-8"
    )

    checks = _by_name(doctor(str(template)))

    assert checks["config_parsable"]["status"] == "ok"
    assert checks["config_complete"]["status"] == "warn"
    for key in PLACEHOLDER_VALUES:
        assert key in checks["config_complete"]["detail"]


def test_doctor_missing_credential_reports_key_only(tmp_path):
    """验证凭据缺失时 config_complete=error，且只报键名与是否配置"""
    no_token = tmp_path / "jenkins-config.yaml"
    no_token.write_text(
        "server:\n  url: http://jenkins.example.com\n  token: ''\n", encoding="utf-8"
    )

    checks = _by_name(doctor(str(no_token)))

    assert checks["config_complete"]["status"] == "error"
    assert "server.token: 未配置" in checks["config_complete"]["detail"]


def test_doctor_path_denied_short_circuits(tmp_path_factory, monkeypatch):
    """验证配置路径越界时 config_path_allowed=error，配置四项全部 skip"""
    outside = tmp_path_factory.mktemp("outside") / "jenkins-config.yaml"
    outside.write_text("server:\n  url: http://x\n  token: y\n", encoding="utf-8")
    # conftest 把 tmp basetemp 整棵树放进了白名单，这里清掉才能构造越界场景
    monkeypatch.delenv("JENKINS_MCP_CONFIG_ROOTS", raising=False)

    result = doctor(str(outside))
    checks = _by_name(result)

    assert checks["config_path_allowed"]["status"] == "error"
    assert "JENKINS_MCP_CONFIG_ROOTS" in checks["config_path_allowed"]["hint"]
    for name in ("config_located", "config_readable", "config_parsable", "config_complete"):
        assert checks[name]["status"] == "skip"
    assert result["status"] == "error"


def test_doctor_never_leaks_token(good_config, monkeypatch):
    """验证返回体 JSON 全文不含 token 原文"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")

    dumped = json.dumps(doctor(str(good_config)), ensure_ascii=False)

    assert TOKEN not in dumped
    assert "server.token: 已配置" in dumped


def test_doctor_log_sink_reports_level_and_sink(good_config):
    """验证 log_sink 报出日志等级与落点，且只判 ok / warn"""
    check = _by_name(doctor(str(good_config)))["log_sink"]

    assert check["status"] in ("ok", "warn")
    assert "级别" in check["detail"] and "落点" in check["detail"]


def test_doctor_log_sink_warns_when_file_handler_missing(good_config, monkeypatch):
    """验证请求了文件日志但未装上文件 handler 时判 warn（不可写路径会降级）"""
    monkeypatch.setattr(
        "jenkins_config.mcp.tools.doctor_tools.current_log_sinks",
        lambda: {
            "level": "WARNING",
            "sinks": ["stderr"],
            "file_sinks": [],
            "initialized": True,
            "env": {"JENKINS_MCP_LOG_LEVEL": "", "JENKINS_MCP_LOG_FILE": "/no/such/dir/x.log"},
        },
    )

    result = doctor(str(good_config))

    assert _by_name(result)["log_sink"]["status"] == "warn"
    # 日志问题不影响 JSON-RPC 会话，因此不得升级为 error
    assert result["status"] != "error"


def test_doctor_runtime_mode_never_escalates(good_config, monkeypatch):
    """验证 runtime_mode 即便被判 error 也不参与整体 status 升级"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")
    monkeypatch.setenv(ALLOWED_HOSTS_ENV_VAR, "jenkins.example.com")
    monkeypatch.setattr(
        "jenkins_config.mcp.tools.doctor_tools._runtime_check",
        lambda: {"name": "runtime_mode", "status": "error", "detail": "人为置错", "hint": ""},
    )

    assert doctor(str(good_config))["status"] == "ok"


def test_doctor_jenkins_check_runs_when_requested(good_config, monkeypatch):
    """验证 include_jenkins=true 时才真正探测，且不回显凭据"""
    from contextlib import contextmanager
    from unittest.mock import Mock

    client = Mock()
    client.base_url = "http://jenkins.example.com"
    client.health_check.return_value = True

    @contextmanager
    def _fake_client(config_path=""):
        yield client

    monkeypatch.setattr(
        "jenkins_config.mcp.tools.doctor_tools.jenkins_client", _fake_client
    )

    result = doctor(str(good_config), include_jenkins=True)
    check = _by_name(result)["jenkins_reachable"]

    assert check["status"] == "ok"
    assert "http://jenkins.example.com" in check["detail"]
    assert TOKEN not in json.dumps(result, ensure_ascii=False)


def test_doctor_jenkins_unreachable_is_error(good_config, monkeypatch):
    """验证连通性检测失败时判 error 并给出下一步"""
    from contextlib import contextmanager

    @contextmanager
    def _boom(config_path=""):
        raise ConnectionError("无法连接")
        yield  # pragma: no cover

    monkeypatch.setattr("jenkins_config.mcp.tools.doctor_tools.jenkins_client", _boom)

    result = doctor(str(good_config), include_jenkins=True)

    assert _by_name(result)["jenkins_reachable"]["status"] == "error"
    assert result["status"] == "error"
    assert result["next_steps"]
