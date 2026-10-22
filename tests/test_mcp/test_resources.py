# tests/test_mcp/test_resources.py
"""
MCP Resources 测试

覆盖：
- 成功路径回可读 JSON
- 失败路径回**与 tool 一致的五字段载荷**（不再是只有 error 一个键的自定义形状）
- "配置可用但历史文件尚未生成"仍回提示载荷，不当成失败

Resource 的 URI 模板装不下 config_path，因此这些用例统一用 JENKINS_MCP_CONFIG
指定配置来源，走的是与 tool 相同的寻址代码。
"""

import json

import pytest

from jenkins_config.mcp.errors import ErrorCode
from jenkins_config.mcp.resources import (
    get_environments_resource,
    get_projects_resource,
    get_recent_builds_resource,
    get_stats_resource,
)
from jenkins_config.paths import CONFIG_ENV_VAR

PAYLOAD_KEYS = {"error_code", "error", "config_path", "next_steps", "docs"}


# ============================================================================
# 成功路径
# ============================================================================


def test_environments_resource_returns_json_list(usable_config, monkeypatch):
    """验证 config://environments 回与环境约定一致的对象列表"""
    monkeypatch.setenv(CONFIG_ENV_VAR, usable_config)

    assert json.loads(get_environments_resource()) == [
        {"name": "dev", "description": "开发环境"}
    ]


def test_projects_resource_returns_json_list(usable_config, monkeypatch):
    """验证带 URI 参数的 config://projects/{env} 照常取到项目"""
    monkeypatch.setenv(CONFIG_ENV_VAR, usable_config)

    payload = json.loads(get_projects_resource("dev"))

    assert payload[0]["environment"] == "dev"
    assert payload[0]["name"] == "project-a"


# ============================================================================
# 失败路径：与 tool 同一种载荷形状
# ============================================================================


def test_missing_config_yields_tool_payload(tmp_path, monkeypatch):
    """验证配置缺失时 resource 回五字段载荷，而不是 {"error": ...}

    客户端可能同时读 tool 和 resource，两种失败形状意味着它得写两套判定；
    一套失败契约才谈得上"机械可分类"。
    """
    monkeypatch.setenv(
        CONFIG_ENV_VAR, str(tmp_path / "nope" / "jenkins-config.yaml")
    )

    payload = json.loads(get_environments_resource())

    assert set(payload) == PAYLOAD_KEYS
    assert payload["error_code"] == ErrorCode.CONFIG_NOT_FOUND
    assert payload["error"].startswith("读取环境列表失败: ")
    assert payload["next_steps"]


def test_broken_yaml_is_classified_as_parse_error(tmp_path, monkeypatch):
    """验证语法坏掉的配置报 config_parse_error，而不是笼统的未知错误

    异常来自 Config.load，但归类仍由 errors 负责：resource 与 tool 给出同一个码，
    客户端照着 next_steps 走一条排障路径就够。
    """
    broken = tmp_path / "jenkins-config.yaml"
    broken.write_text("server:\n  url: [unclosed\n", encoding="utf-8")
    monkeypatch.setenv(CONFIG_ENV_VAR, str(broken))

    payload = json.loads(get_environments_resource())

    assert payload["error_code"] == ErrorCode.CONFIG_PARSE_ERROR
    assert payload["config_path"] == str(broken.resolve())


# ============================================================================
# 历史类 resource：没有历史文件是合法状态
# ============================================================================


@pytest.mark.parametrize(
    "loader", [get_recent_builds_resource, get_stats_resource],
    ids=["history://recent", "history://stats"],
)
def test_missing_history_file_is_a_hint_not_a_failure(usable_config, monkeypatch, loader):
    """验证"配置可用但还没构建过"回 message 提示，不套失败载荷

    给这种合法状态打上 error_code 会让客户端把"没构建过"读成"查询出错了"。
    """
    monkeypatch.setenv(CONFIG_ENV_VAR, usable_config)

    payload = json.loads(loader())

    assert "message" in payload
    assert "error_code" not in payload
