# tests/test_mcp/test_errors.py
"""
统一失败载荷测试

覆盖：
- 每个错误码的 next_steps 非空，且每条都是可执行动作
- failure_payload 的字段集合固定
- classify 按 phase 区分 PermissionError 的两种含义
"""

import json

import pytest

from jenkins_config.config_io import VALIDATION_ERROR_PREFIX
from jenkins_config.mcp.errors import (
    NEXT_STEPS,
    ErrorCode,
    classify,
    failure_payload,
)

# 可执行动作的标志：调 tool、设环境变量、或明确的文件/权限操作
# 用"包含"而不是"以…开头"：中文动作常带状语前缀（"在客户端 mcp.json 的 env 中设置 X"），
# 卡开头只会逼着把文案写成生硬的祈使句。
ACTIONABLE_MARKERS = (
    "调用",
    "设置",
    "编辑",
    "改用",
    "补齐",
    "查看",
    "追加",
    "重启",
    "传",
)

ALL_CODES = [
    value
    for name, value in vars(ErrorCode).items()
    if not name.startswith("_") and isinstance(value, str)
]


def test_all_error_codes_have_next_steps():
    """验证每个错误码都登记了默认下一步动作"""
    for code in ALL_CODES:
        assert code in NEXT_STEPS, f"错误码 {code} 未登记 next_steps"
        assert NEXT_STEPS[code], f"错误码 {code} 的 next_steps 为空"


@pytest.mark.parametrize("code", ALL_CODES)
def test_next_steps_are_actionable(code):
    """验证每条 next_steps 都含可执行动作，而非"请检查配置"式描述"""
    for step in NEXT_STEPS[code]:
        assert any(marker in step for marker in ACTIONABLE_MARKERS), (
            f"{code} 的建议不含可执行动作: {step}"
        )


def test_failure_payload_field_set_is_fixed():
    """验证失败载荷字段集合固定为五个键"""
    payload = failure_payload(ErrorCode.CONFIG_NOT_FOUND, "配置文件不存在", "/tmp/x.yaml")

    assert set(payload) == {"error_code", "error", "config_path", "next_steps", "docs"}
    assert payload["error_code"] == "config_not_found"
    assert payload["config_path"] == "/tmp/x.yaml"
    assert payload["docs"]


def test_failure_payload_defaults_next_steps_by_code():
    """验证未显式给 next_steps 时按 code 取默认值"""
    payload = failure_payload(ErrorCode.WRITE_NOT_ALLOWED, "已禁止写操作")

    assert payload["next_steps"] == NEXT_STEPS[ErrorCode.WRITE_NOT_ALLOWED]


def test_failure_payload_keeps_explicit_next_steps():
    """验证显式传入的 next_steps 优先于默认值"""
    payload = failure_payload(ErrorCode.INVALID_TARGET, "环境不存在", "", ["调用 list_environments"])

    assert payload["next_steps"] == ["调用 list_environments"]


def test_failure_payload_unknown_code_falls_back():
    """验证未登记的错误码退回 UNKNOWN 的默认动作，而不是留空"""
    payload = failure_payload("brand_new_code", "未知失败")

    assert payload["next_steps"] == NEXT_STEPS[ErrorCode.UNKNOWN]


def test_failure_payload_is_json_serializable():
    """验证载荷可直接 JSON 序列化（MCP 返回体必须可序列化）"""
    assert json.loads(json.dumps(failure_payload(ErrorCode.UNKNOWN, "x")))


def test_classify_distinguishes_permission_error_by_phase():
    """验证白名单越界与文件权限失败靠 phase 区分（异常类型相同）"""
    denied = PermissionError("配置文件路径不在允许范围内")

    assert classify(denied, "resolve") == ErrorCode.CONFIG_PATH_DENIED
    assert classify(denied, "read") == ErrorCode.CONFIG_PERMISSION_DENIED


def test_classify_missing_file():
    """验证文件不存在归入 config_not_found"""
    assert classify(FileNotFoundError("no such file"), "read") == ErrorCode.CONFIG_NOT_FOUND


def test_classify_yaml_error_is_parse_error():
    """验证 YAML 非法归入 config_parse_error（yaml.YAMLError 不是 ValueError）"""
    import yaml

    with pytest.raises(yaml.YAMLError) as exc_info:
        yaml.safe_load("a:\n b: [1,\n")

    assert classify(exc_info.value, "parse") == ErrorCode.CONFIG_PARSE_ERROR


def test_classify_json_error_is_parse_error():
    """验证 JSON 非法归入 config_parse_error（JSONDecodeError 是 ValueError 子类）"""
    with pytest.raises(json.JSONDecodeError) as exc_info:
        json.loads("{")

    assert classify(exc_info.value, "parse") == ErrorCode.CONFIG_PARSE_ERROR


def test_classify_validation_error_is_incomplete():
    """验证必填字段为空归入 config_incomplete，而非 parse_error

    两者都是 ValueError，靠 config_io.VALIDATION_ERROR_PREFIX 这一共用常量分流：
    "字段没填"要去编辑配置，"语法坏了"要去修 YAML，建议完全不同。
    """
    exc = ValueError(f"{VALIDATION_ERROR_PREFIX}server.url 不能为空")

    assert classify(exc, "parse") == ErrorCode.CONFIG_INCOMPLETE


def test_classify_key_error_is_incomplete():
    """验证项目缺 name 键归入 config_incomplete"""
    assert classify(KeyError("name"), "parse") == ErrorCode.CONFIG_INCOMPLETE


def test_classify_runtime_error_is_home_unavailable():
    """验证家目录不可解析归入 home_unavailable"""
    exc = RuntimeError("Could not determine home directory")

    assert classify(exc, "resolve") == ErrorCode.HOME_UNAVAILABLE


def test_classify_os_error_is_permission_denied():
    """验证读目录这类 OSError 统一归入 config_permission_denied

    Windows 抛 PermissionError、Linux 抛 IsADirectoryError，
    两者都是 OSError 子类，错误码不应因平台而异。
    """
    assert classify(IsADirectoryError("是目录"), "read") == ErrorCode.CONFIG_PERMISSION_DENIED
    assert classify(OSError("io 失败"), "read") == ErrorCode.CONFIG_PERMISSION_DENIED


def test_classify_unknown_exception():
    """验证无法归类的异常落到 UNKNOWN"""
    assert classify(Exception("统计失败"), "read") == ErrorCode.UNKNOWN
