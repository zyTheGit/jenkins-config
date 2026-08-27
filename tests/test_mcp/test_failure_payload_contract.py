# tests/test_mcp/test_failure_payload_contract.py
"""统一失败载荷的跨 tool 契约测试（QA 补漏）

PRD AC-12 要求"配置文件不存在时，list_environments / list_projects /
show_config / health_check / show_history / trigger_build 的失败返回均含
error_code='config_not_found'、非空 next_steps、config_path"。

D1 / D2 修复后这里全部改为正向断言。get_build_log 的返回类型仍是 str
（交付总监裁定本轮不改对外接口），因此对它只断言"文案里带可执行动作"。
另有一条反向用例锁定 D1 的修复边界：配置正常而历史文件尚未生成是合法状态，
必须仍返回空列表 / 全 0 统计。
"""

import pytest

from jenkins_config.mcp.tools.build_tools import rebuild_last, trigger_build
from jenkins_config.mcp.tools.config_tools import list_environments, list_projects, save_config, show_config
from jenkins_config.mcp.tools.diagnose_tools import get_build_log, get_build_status, health_check
from jenkins_config.mcp.tools.doctor_tools import doctor
from jenkins_config.mcp.tools.history_tools import show_history, show_history_stats
from jenkins_config.mcp.tools.init_tools import init_config
from jenkins_config.mcp.utils import WRITE_ENV_VAR

PAYLOAD_KEYS = {"error_code", "error", "config_path", "next_steps", "docs"}


@pytest.fixture
def missing_config(tmp_path):
    """返回一个位于白名单内、但确实不存在的配置文件路径"""
    return str(tmp_path / "nope" / "jenkins-config.yaml")


@pytest.mark.parametrize("tool", [list_environments, list_projects])
def test_list_tools_return_single_pure_payload(tool, missing_config):
    """验证 list 型 tool 失败时返回单元素纯载荷，且不含伪业务字段"""
    result = tool(config_path=missing_config)

    assert len(result) == 1
    assert set(result[0]) == PAYLOAD_KEYS
    assert result[0]["error_code"] == "config_not_found"
    assert result[0]["next_steps"]
    assert result[0]["config_path"]


def test_dict_tools_merge_payload_at_top_level(missing_config):
    """验证 dict 型 tool 失败时五字段合并在顶层，health_check 保留既有键"""
    summary = show_config(config_path=missing_config)
    probe = health_check(config_path=missing_config)

    assert PAYLOAD_KEYS.issubset(summary)
    assert summary["error_code"] == "config_not_found"
    assert PAYLOAD_KEYS.issubset(probe)
    assert probe["reachable"] is False and "url" in probe


def test_show_history_reports_config_not_found(missing_config):
    """验证配置文件不存在时 show_history 给出统一失败载荷而非空结果"""
    result = show_history(config_path=missing_config)

    assert len(result) == 1
    assert set(result[0]) == PAYLOAD_KEYS
    assert result[0]["error_code"] == "config_not_found"
    assert result[0]["next_steps"]
    assert result[0]["config_path"]


def test_show_history_stats_reports_config_not_found(missing_config):
    """验证配置文件不存在时 show_history_stats 给出统一失败载荷而非全 0 统计"""
    result = show_history_stats(config_path=missing_config)

    assert PAYLOAD_KEYS.issubset(result)
    assert result["error_code"] == "config_not_found"
    assert result["next_steps"]


def test_history_tools_keep_empty_semantics_when_config_usable(usable_config):
    """验证 D1 修复不误伤"配置正常但还没构建过"这一合法状态"""
    assert show_history(config_path=usable_config) == []

    stats = show_history_stats(config_path=usable_config)

    assert "error_code" not in stats
    assert stats["total"] == 0
    assert stats["success"] == 0
    assert stats["failure"] == 0
    assert stats["success_rate"] == "0%"


def test_get_build_status_carries_payload(missing_config):
    """验证 get_build_status 失败时带 error_code 与可执行的下一步，且形状不变"""
    result = get_build_status("folder/my-job", 1, config_path=missing_config)

    assert result["number"] == 1
    assert result["status"] == "UNKNOWN"
    assert result["result"] is None
    assert PAYLOAD_KEYS.issubset(result)
    assert result["error_code"] == "config_not_found"
    assert result["next_steps"]


def test_get_build_log_error_points_to_next_action(missing_config):
    """验证 get_build_log 的失败文案带可执行动作与配置类错误码

    返回类型仍是 str（本轮不改对外接口），所以只能断言文案内容。
    """
    text = get_build_log("folder/my-job", 1, config_path=missing_config)

    assert any(tool in text for tool in ("doctor", "where_config", "init_config"))
    assert "config_not_found" in text


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda path: trigger_build(env="dev", config_path=path), id="trigger_build"
        ),
        pytest.param(lambda path: rebuild_last(config_path=path), id="rebuild_last"),
    ],
)
def test_build_tools_keep_container_and_carry_payload(call, missing_config, monkeypatch):
    """验证写类 tool 在配置缺失时保留 triggered / failed 容器并补齐五字段

    先放开写门控，否则失败原因会变成 write_not_allowed，测不到配置寻址这条路径。
    """
    monkeypatch.setenv(WRITE_ENV_VAR, "1")

    result = call(missing_config)

    assert result["triggered"] == []
    assert result["failed"]
    assert PAYLOAD_KEYS.issubset(result)
    assert result["error_code"] == "config_not_found"
    assert result["next_steps"]


def test_save_config_carries_payload_when_write_denied(missing_config, monkeypatch):
    """验证 save_config 在写门控关闭时回 write_not_allowed 而不是裸异常"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)

    result = save_config(config_path=missing_config)

    assert PAYLOAD_KEYS.issubset(result)
    assert result["error_code"] == "write_not_allowed"
    assert result["next_steps"]


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        ({"target": "nowhere"}, "invalid_target"),
        ({"target": "cwd", "format": "json"}, "invalid_target"),
    ],
)
def test_init_config_failures_carry_payload(kwargs, expected_code):
    """验证 init_config 的入参校验失败同样回五字段载荷且 created=False"""
    result = init_config(**kwargs)

    assert result["created"] is False
    assert PAYLOAD_KEYS.issubset(result)
    assert result["error_code"] == expected_code
    assert result["next_steps"]


def test_doctor_reports_next_steps_when_config_missing(missing_config):
    """验证 doctor 在配置缺失时给出非 ok 状态与非空 next_steps

    doctor 的对外形状是 status / checks / next_steps（不是五字段载荷），
    这里锁定"坏了必须给下一步动作"这一条契约。
    """
    result = doctor(config_path=missing_config)

    assert result["status"] in ("warn", "error")
    assert result["next_steps"]
    assert any(check["name"] == "config_located" for check in result["checks"])
