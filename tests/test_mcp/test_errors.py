# tests/test_mcp/test_errors.py
"""
统一失败载荷测试

覆盖：
- 每个错误码的 next_steps 非空，且每条都是可执行动作
- failure_payload 的字段集合固定
- classify 按 phase 区分 PermissionError 的两种含义
- config_failure_payload 从异常 + 路径补全载荷（含越界、缺文件、无异常三种）
- unusable_config_payload / is_config_unusable 共用 CONFIG_UNUSABLE_CODES 这一份判定
"""

import json

import pytest

from jenkins_config.config_load import VALIDATION_ERROR_PREFIX
from jenkins_config.mcp.errors import (
    CONFIG_UNUSABLE_CODES,
    NEXT_STEPS,
    ErrorCode,
    classify,
    config_failure_payload,
    failure_payload,
    failure_text,
    is_config_unusable,
    unusable_config_payload,
)
from jenkins_config.mcp.utils import (
    CONFIG_ENV_VAR,
    CONFIG_ROOTS_ENV_VAR,
    ConfigInspection,
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

    两者都是 ValueError，靠 config_load.VALIDATION_ERROR_PREFIX 这一共用常量分流：
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


# ============================================================================
# 载荷构造：文案、异常、探测结论
# ============================================================================


def test_failure_text_prefixes_the_action():
    """验证带动作前缀时拼成 "动作: 原因" """
    assert failure_text("加载配置失败", "语法错误") == "加载配置失败: 语法错误"
    assert failure_text("加载配置失败", ValueError("缩进不对")) == "加载配置失败: 缩进不对"


def test_failure_text_without_action_keeps_bare_reason():
    """验证 action 为空时不回 ": 原因" 这种开头带悬空冒号的文案"""
    assert failure_text("", ValueError("缩进不对")) == "缩进不对"


@pytest.mark.parametrize("code", sorted(CONFIG_UNUSABLE_CODES))
def test_is_config_unusable_true_for_unusable_subset(code):
    """验证 CONFIG_UNUSABLE_CODES 里的每个码都被判为配置不可用"""
    assert is_config_unusable(failure_payload(code, "x")) is True


def test_is_config_unusable_false_for_other_codes():
    """验证 config_incomplete 与业务类错误码不被判为"配置不可用"

    凭据没填不影响读本地历史；把它也算成不可用会让零配置用户的
    show_history 直接失败，而它本该返回空列表。
    """
    for code in (
        ErrorCode.CONFIG_INCOMPLETE,
        ErrorCode.WRITE_NOT_ALLOWED,
        ErrorCode.INVALID_TARGET,
        ErrorCode.UNKNOWN,
    ):
        assert is_config_unusable(failure_payload(code, "x")) is False


def test_config_failure_payload_drops_denied_path(tmp_path, monkeypatch):
    """验证越界路径折算成 config_path_denied，且不把越界路径写进载荷

    config_path 是"当前正在读哪个配置"的权威答案，塞一个没通过白名单的路径
    进去等于让载荷自相矛盾。
    """
    outside = tmp_path / "outside" / "jenkins-config.yaml"
    outside.parent.mkdir()
    monkeypatch.setenv(CONFIG_ROOTS_ENV_VAR, str(tmp_path / "nowhere"))
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)

    payload = config_failure_payload(str(outside), ValueError("坏语法"), "加载配置失败")

    assert payload["error_code"] == "config_path_denied"
    assert payload["config_path"] == ""
    assert payload["error"].startswith("加载配置失败: ")


def test_config_failure_payload_reports_resolved_path_for_read_errors(tmp_path):
    """验证寻址成功时按读取阶段归类异常，并回显解析后的路径"""
    missing = tmp_path / "jenkins-config.yaml"

    payload = config_failure_payload(str(missing), OSError("没有读权限"), "加载配置失败")

    assert payload["error_code"] == "config_permission_denied"
    assert payload["config_path"] == str(missing.resolve())
    assert payload["next_steps"]


def test_config_failure_payload_maps_missing_file_to_not_found(tmp_path):
    """验证 FileNotFoundError 走同一入口时归入 config_not_found"""
    missing = tmp_path / "jenkins-config.yaml"

    payload = config_failure_payload(str(missing), FileNotFoundError("no such file"))

    assert payload["error_code"] == "config_not_found"
    assert payload["error"] == "no such file"


def test_config_failure_payload_without_exception_falls_back_to_unknown(tmp_path):
    """验证只给出 action、没有异常时回 unknown_error 而不是编造一个配置类错误码"""
    existing = tmp_path / "jenkins-config.yaml"
    existing.write_text("server: {}\n", encoding="utf-8")

    payload = config_failure_payload(str(existing), None, "查询历史统计失败")

    assert payload["error_code"] == "unknown_error"
    assert payload["error"] == "查询历史统计失败"


def _inspection(**overrides: object) -> ConfigInspection:
    """构造一份"五层全通过"的探测结果，按需覆盖字段

    Args:
        overrides: 需要覆盖的字段名与取值

    Returns:
        ConfigInspection 实例

    Example:
        >>> _inspection(error_code="config_not_found").error_code
        'config_not_found'
    """
    base: dict[str, object] = {
        "config_path": "/tmp/jenkins-config.yaml",
        "source": "probe",
        "exists": True,
        "readable": True,
        "parse_ok": True,
        "complete": True,
        "path_allowed": True,
    }
    base.update(overrides)
    return ConfigInspection(**base)  # type: ignore[arg-type]


def test_unusable_config_payload_short_circuits_with_action_prefix():
    """验证配置不可用时直接取探测结论组装载荷，错误码与路径不再二次推算"""
    inspection = _inspection(
        exists=False,
        readable=False,
        parse_ok=False,
        complete=False,
        error_code=ErrorCode.CONFIG_NOT_FOUND,
        error="配置文件不存在: /tmp/jenkins-config.yaml",
    )

    payload = unusable_config_payload(inspection, "查询历史记录失败")

    assert payload is not None
    assert set(payload) == {"error_code", "error", "config_path", "next_steps", "docs"}
    assert payload["error_code"] == "config_not_found"
    assert payload["error"].startswith("查询历史记录失败: ")
    assert payload["config_path"] == "/tmp/jenkins-config.yaml"


def test_unusable_config_payload_passes_usable_and_incomplete_configs():
    """验证探测通过、以及"仅凭据没填"两种情形都不短路"""
    assert unusable_config_payload(_inspection(), "查询历史记录失败") is None

    incomplete = _inspection(
        complete=False,
        placeholder_fields=["server.url", "server.token"],
        error_code=ErrorCode.CONFIG_INCOMPLETE,
        error="配置仍是模板占位符，未填写的字段: server.url, server.token",
    )

    assert unusable_config_payload(incomplete, "查询历史记录失败") is None


def test_every_error_code_reference_resolves():
    """验证包内引用的 ErrorCode 属性名都真实存在

    这些分支大多只在并发占用、权限异常这类场景才走到（例如 init_config 的
    锁超时），写错的属性名不会当场暴露，而是把"结构化失败载荷"换成一个
    抛给客户端的 AttributeError——比载荷写错严重得多。
    """
    import ast
    from pathlib import Path

    import jenkins_config.mcp as mcp_package

    package_dir = Path(mcp_package.__file__).parent
    referenced: set[str] = set()
    for module in package_dir.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "ErrorCode"
            ):
                referenced.add(node.attr)

    assert referenced, "未发现任何 ErrorCode 引用，说明本用例的扫描方式失效了"
    missing = sorted(name for name in referenced if not hasattr(ErrorCode, name))
    assert not missing, f"ErrorCode 上不存在这些属性: {missing}"
