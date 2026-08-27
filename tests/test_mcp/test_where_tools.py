# tests/test_mcp/test_where_tools.py
"""
where_config 诊断工具测试

测试覆盖：
- JENKINS_MCP_CONFIG 为绝对路径时 source='env_var'、env_var.effective=true
- 该变量为相对路径时 effective=false 且 source 回退为 probed/fallback
- AC-06：返回体全文不含配置里的 token 原始字符（只做路径探测，不读内容）
- 字段集合与 show_config 无交集
- 过宽候选目录标 skipped_reason='too_broad'、allowed=false
- 越界路径只回 path_allowed=false + 失败载荷，不回 exists / history_path


注意 conftest 的 autouse fixture 会把 pytest basetemp 注入
JENKINS_MCP_CONFIG_ROOTS，因此这里不断言 allowed_config_bases 的长度；
需要构造"越界"场景时改用 monkeypatch 覆盖该变量。
"""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jenkins_config import paths
from jenkins_config.config_types import BuildConfig, ServerConfig
from jenkins_config.mcp.tools.where_tools import where_config


TOKEN = "SUPERSECRETTOKEN123456"


@pytest.fixture
def config_with_token(tmp_path):
    """写入一份含真实 token 的配置文件

    Returns:
        配置文件路径
    """
    target = tmp_path / "jenkins-config.yaml"
    target.write_text(
        "server:\n"
        "  url: http://jenkins.example.com\n"
        "  username: admin\n"
        f"  token: {TOKEN}\n",
        encoding="utf-8",
    )
    return target


# ============================================================================
# 环境变量折算测试
# ============================================================================


def test_absolute_env_var_marked_effective(config_with_token, monkeypatch):
    """验证绝对路径的 JENKINS_MCP_CONFIG 生效：source='env_var'、effective=true"""
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(config_with_token))

    result = where_config()

    assert result["source"] == "env_var"
    assert result["config_path"] == str(config_with_token)
    assert result["exists"] is True
    assert result["env_var"] == {
        "name": paths.CONFIG_ENV_VAR,
        "value": str(config_with_token),
        "effective": True,
    }


def test_relative_env_var_is_ignored(tmp_path, monkeypatch):
    """验证相对路径的 JENKINS_MCP_CONFIG 不生效，source 回退到自动探测两态"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, "jenkins-config.yaml")

    result = where_config()

    assert result["env_var"]["effective"] is False
    assert result["env_var"]["value"] == "jenkins-config.yaml"
    assert result["source"] in ("probed", "fallback")


def test_explicit_arg_wins_over_env_var(config_with_token, tmp_path, monkeypatch):
    """验证显式参数优先于环境变量，此时 env_var.effective=false"""
    other = tmp_path / "other.yaml"
    other.write_text("server: {}", encoding="utf-8")
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(config_with_token))

    result = where_config(str(other))

    assert result["source"] == "explicit_arg"
    assert result["config_path"] == str(other)
    assert result["env_var"]["effective"] is False


def test_fallback_implies_not_exists(tmp_path, monkeypatch):
    """验证 source='fallback' 时 exists 必为 false"""
    root = tmp_path / "repo"
    (root / "jenkins_config").mkdir(parents=True)
    monkeypatch.setattr(paths, "__file__", str(root / "jenkins_config" / "paths.py"))
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(paths, "user_config_dir", lambda: empty)
    monkeypatch.delenv(paths.CONFIG_ENV_VAR, raising=False)

    result = where_config()

    assert result["source"] == "fallback"
    assert result["exists"] is False


# ============================================================================
# AC-06 脱敏与字段边界测试
# ============================================================================


def test_payload_never_contains_token(config_with_token, monkeypatch):
    """验证返回体全文不含 token 原始字符（不加载配置内容，脱敏靠"不读"）"""
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(config_with_token))

    dumped = json.dumps(where_config(), ensure_ascii=False)
    dumped_explicit = json.dumps(
        where_config(str(config_with_token)), ensure_ascii=False
    )

    assert TOKEN not in dumped
    assert TOKEN not in dumped_explicit
    assert "SUPERSECRET" not in dumped


def test_field_set_does_not_overlap_show_config(config_with_token, monkeypatch):
    """验证与 show_config 字段集合无交集（两个 tool 各答一个问题）"""
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(config_with_token))

    config = Mock()
    config.server = ServerConfig(
        url="http://jenkins:8080", username="admin", token=TOKEN
    )
    config.build = BuildConfig()
    config.list_environments.return_value = []

    with patch(
        "jenkins_config.mcp.tools.config_tools.get_config", return_value=config
    ):
        from jenkins_config.mcp.tools.config_tools import show_config

        show_keys = set(show_config())

    where_keys = set(where_config())

    assert where_keys == {
        "config_path",
        "exists",
        "path_allowed",
        "source",
        "env_var",
        "mode",
        "search_bases",
        "candidate_file_names",
        "history_path",
        "allowed_config_bases",
    }
    assert where_keys & show_keys == set()


# ============================================================================
# 候选目录明细测试
# ============================================================================


def test_search_bases_carry_order_and_allowed(config_with_token, monkeypatch):
    """验证每个候选都带 order（从 1 递增）与 allowed 布尔"""
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(config_with_token))

    bases = where_config()["search_bases"]

    assert [item["order"] for item in bases] == list(range(1, len(bases) + 1))
    assert all(isinstance(item["allowed"], bool) for item in bases)
    assert all(
        set(item) == {
            "base",
            "order",
            "kind",
            "exists",
            "matched_file",
            "skipped_reason",
            "allowed",
        }
        for item in bases
    )


def test_home_base_marked_too_broad(config_with_token, monkeypatch):
    """验证家目录这类过宽候选被标 too_broad 且 allowed=false"""
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(config_with_token))
    monkeypatch.setattr(paths, "user_config_dir", Path.home)

    user_base = where_config()["search_bases"][-1]

    assert user_base["kind"] == "user_config_dir"
    assert user_base["skipped_reason"] == "too_broad"
    assert user_base["allowed"] is False


def test_reports_path_outside_allowed_bases(tmp_path, monkeypatch):
    """验证越界路径只回 path_allowed=false 与出路，不回 exists / history_path

    exists 一旦对任意路径如实回报，本 tool 就成了文件存在性探针，
    把 resolve_config_path 的白名单绕了过去。
    """
    from jenkins_config.mcp.utils import CONFIG_ROOTS_ENV_VAR

    outside = tmp_path / "outside" / "jenkins-config.yaml"
    outside.parent.mkdir()
    outside.write_text("server: {}", encoding="utf-8")
    # 覆盖 conftest 对整个 basetemp 的放行，构造真正的"越界"
    monkeypatch.setenv(CONFIG_ROOTS_ENV_VAR, str(tmp_path / "nowhere"))
    monkeypatch.delenv(paths.CONFIG_ENV_VAR, raising=False)

    result = where_config(str(outside))

    assert result["config_path"] == str(outside)
    assert result["path_allowed"] is False
    assert "exists" not in result
    assert "history_path" not in result
    assert result["error_code"] == "config_path_denied"
    assert result["next_steps"]
    assert str(outside.parent) not in result["allowed_config_bases"]


def test_allowed_path_keeps_exists_and_history(config_with_token, monkeypatch):
    """验证白名单内的路径仍照常回报 exists / history_path"""
    monkeypatch.delenv(paths.CONFIG_ENV_VAR, raising=False)

    result = where_config(str(config_with_token))

    assert result["path_allowed"] is True
    assert result["exists"] is True
    assert result["history_path"].endswith("build_history.json")
    assert "error_code" not in result
