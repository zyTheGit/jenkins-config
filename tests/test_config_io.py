"""
配置 I/O 模块测试

测试覆盖：
- Config.save() YAML 写入
- config_to_dict / _project_to_dict 序列化
- generate_template / show_template
- _parse_params_field 边界情况
"""

from pathlib import Path

import pytest
import yaml

from jenkins_config.config import Config
from jenkins_config.config_types import (
    ServerConfig,
    BuildConfig,
    Environment,
    Project,
)
from jenkins_config.config_io import (
    config_to_dict,
    _project_to_dict,
    _parse_params_field,
    generate_template,
    show_template,
)


# ============================================================================
# 保存
# ============================================================================


def _make_config(**kwargs):
    """快捷创建 Config（规避 dataclass 嵌套构造）"""
    return Config(
        server=ServerConfig(url=kwargs.pop("server_url", "http://localhost:8080"),
                            token=kwargs.pop("server_token", "t")),
        build=BuildConfig(mode=kwargs.pop("build_mode", "parallel")),
        branch_field=kwargs.pop("branch_field", "branch"),
    )


def test_save_yaml(tmp_path):
    """保存为 YAML 且可重新加载"""
    config_file = tmp_path / "test.yaml"

    config = _make_config(server_url="http://localhost:8080", server_token="s3cret",
                          build_mode="sequential")
    config.environments = {
        "dev": Environment(
            name="dev",
            description="开发环境",
            params={"BRANCH": "develop"},
            projects=[Project(name="app-a", params={"BRANCH": "feature-x"})],
        )
    }

    config.save(str(config_file))
    assert config_file.exists()

    raw = config_file.read_text(encoding="utf-8")
    assert "s3cret" in raw

    reloaded = Config.load(str(config_file))
    assert reloaded.server.url == "http://localhost:8080"
    assert reloaded.build.mode == "sequential"
    assert reloaded.environments["dev"].params["BRANCH"] == "develop"
    assert reloaded.environments["dev"].projects[0].params["BRANCH"] == "feature-x"


# ============================================================================
# 序列化
# ============================================================================


def test_config_to_dict_minimal():
    """最简配置转字典"""
    config = _make_config()
    d = config_to_dict(config)

    assert d["server"]["url"] == "http://localhost:8080"
    assert d["server"]["token"] == "t"
    assert d["build"]["mode"] == "parallel"
    assert "branch_field" not in d  # 默认值不输出
    assert "environments" not in d  # 空则不输出


def test_config_to_dict_full():
    """完整配置转字典"""
    config = _make_config(branch_field="BRANCH")
    config.environments = {
        "prod": Environment(
            name="prod",
            description="生产",
            branch_field="GIT_BRANCH",
            params={"GIT_BRANCH": "main"},
            projects=[
                Project(name="proj-a", path="folder/proj-a", params={"GIT_BRANCH": "release"})
            ],
        )
    }

    d = config_to_dict(config)

    assert d["branch_field"] == "BRANCH"
    assert "prod" in d["environments"]
    env = d["environments"]["prod"]
    assert env["description"] == "生产"
    assert env["branch_field"] == "GIT_BRANCH"
    assert env["params"]["GIT_BRANCH"] == "main"
    assert env["projects"][0]["name"] == "proj-a"
    assert env["projects"][0]["path"] == "folder/proj-a"


def test_project_to_dict():
    """项目转字典，仅输出有值的字段"""
    from jenkins_config.config_types import Project

    # 只有 name
    d = _project_to_dict(Project(name="simple"))
    assert d == {"name": "simple"}

    # name + path（与 name 不同时才输出 path）
    d = _project_to_dict(Project(name="a", path="custom/a"))
    assert d == {"name": "a", "path": "custom/a"}

    # name + params
    d = _project_to_dict(Project(name="a", params={"X": "y"}))
    assert d == {"name": "a", "params": {"X": "y"}}


# ============================================================================
# 模板
# ============================================================================


def test_generate_template():
    """生成模板包含所有必需字段"""
    tpl = generate_template()
    assert "server" in tpl
    assert "url" in tpl["server"]
    assert "token" in tpl["server"]
    assert "build" in tpl
    assert "branch_field" in tpl
    assert "environments" in tpl
    assert "dev" in tpl["environments"]
    assert "prod" in tpl["environments"]


def test_show_template(capsys):
    """模板说明输出到 stdout"""
    show_template()
    captured = capsys.readouterr()
    assert "Jenkins 配置文件模板" in captured.out
    assert "branch_field" in captured.out
    assert "environments" in captured.out


# ============================================================================
# 参数解析边界
# ============================================================================


def test_parse_params_field_none():
    """传入 None 返回空字典"""
    assert _parse_params_field(None) == {}


def test_parse_params_field_empty_string():
    """空字符串返回空字典"""
    assert _parse_params_field("") == {}
    assert _parse_params_field("   ") == {}


def test_parse_params_field_invalid_type():
    """传入 int 等非法类型返回空字典"""
    assert _parse_params_field(123) == {}
    assert _parse_params_field([]) == {}
    assert _parse_params_field(True) == {}


# ============================================================================
# 加载边界
# ============================================================================


def test_load_empty_environments(tmp_path):
    """environments 为空字典"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n  url: http://localhost:8080\n  token: t\nenvironments: {}\n"
    )
    config = Config.load(str(config_file))
    assert config.get_jobs() == []


def test_load_no_optional_fields(tmp_path):
    """只填必填字段也能加载"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n  url: http://localhost:8080\n  token: t\n"
    )
    config = Config.load(str(config_file))
    assert config.server.url == "http://localhost:8080"
    assert config.build.mode == "parallel"  # 默认值
    assert config.build.queue_timeout == 30  # 队列等待超时默认值


def test_load_queue_timeout_custom(tmp_path):
    """build.queue_timeout 可从配置文件读取，且保存后不丢失"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "build:\n  queue_timeout: 120\n",
        encoding="utf-8",
    )
    config = Config.load(str(config_file))
    assert config.build.queue_timeout == 120

    # 序列化应保留该字段
    d = config_to_dict(config)
    assert d["build"]["queue_timeout"] == 120


def test_load_project_without_path(tmp_path):
    """项目无 path 时使用 name 作为 path"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text(
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments:\n"
        "  dev:\n"
        "    projects:\n"
        "      - name: my-app\n"
    )
    config = Config.load(str(config_file))
    jobs = config.get_jobs(env="dev")
    assert jobs[0].path == "my-app"


# ============================================================================
# 配置加载边界
# ============================================================================


def test_load_yaml_not_dict(tmp_path):
    """YAML 文件内容不是字典时抛 ValueError"""
    config_file = tmp_path / "test.yaml"
    config_file.write_text("just a string", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML 配置文件格式错误"):
        Config.load(str(config_file))


def test_load_unknown_suffix_fallback_json(tmp_path):
    """未知后缀尝试按 JSON 加载"""
    config_file = tmp_path / "test.conf"
    config_file.write_text("""{"server": {"url": "http://localhost:8080", "token": "t"}, "environments": {}}""", encoding="utf-8")
    config = Config.load(str(config_file))
    assert config.server.url == "http://localhost:8080"


# ============================================================================
# 模板占位符与判据同源（防漂移）
# ============================================================================


def test_template_uses_placeholder_values():
    """模板的 server.url / server.token 必须恒等于 PLACEHOLDER_VALUES

    doctor 的 config_complete 靠占位符比对判断"配置填过没有"。若模板改了字面量
    而判据没跟着改，一份从未填写的配置会被报成"已完成"——比没有这项检查更糟。
    """
    from jenkins_config.config_io import PLACEHOLDER_VALUES, generate_template

    template = generate_template()

    assert template["server"]["url"] == PLACEHOLDER_VALUES["server.url"]
    assert template["server"]["token"] == PLACEHOLDER_VALUES["server.token"]


def test_placeholder_values_keys_are_dotted_paths():
    """占位符键名是配置内的点分路径，便于直接回报给用户"""
    from jenkins_config.config_io import PLACEHOLDER_VALUES

    assert set(PLACEHOLDER_VALUES) == {"server.url", "server.token"}
    for value in PLACEHOLDER_VALUES.values():
        assert value.strip(), "占位符必须非空，否则会被 _validate_config 直接拒掉"


def test_placeholder_template_passes_validation(tmp_path):
    """模板占位符能通过必填校验：这正是"必须靠占位符比对"的原因"""
    from jenkins_config.config_io import generate_template

    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text(
        yaml.safe_dump(generate_template(), allow_unicode=True), encoding="utf-8"
    )

    config = Config.load(str(config_file))

    assert config.server.url and config.server.token


def test_validation_error_prefix_is_shared():
    """必填校验的错误前缀取自共用常量（mcp/errors.classify 依赖它分流）"""
    from jenkins_config.config_io import VALIDATION_ERROR_PREFIX, _validate_config

    with pytest.raises(ValueError) as exc_info:
        _validate_config(ServerConfig(url="", username="admin", token="t"))

    assert str(exc_info.value).startswith(VALIDATION_ERROR_PREFIX)


# ============================================================================
# T-08 / T-09：模板字段清单与模板文本
# ============================================================================


def test_template_fields_marks_required_keys():
    """必填项须覆盖 server.url / server.token / environments"""
    from jenkins_config.config_io import template_fields

    fields = template_fields()
    required = {item["key"] for item in fields if item["required"]}

    assert {"server.url", "server.token", "environments"} <= required
    for item in fields:
        assert set(item) == {"key", "description", "required"}
        assert item["description"].strip()


def test_template_fields_share_source_with_show_template(capsys):
    """字段清单与 CLI 的 show_template 输出同源（文案不许各写一套）"""
    from jenkins_config.config_io import show_template, template_fields

    show_template()
    out = capsys.readouterr().out

    for item in template_fields():
        assert item["key"] in out
        assert item["description"] in out


def test_template_text_yaml_round_trips():
    """template_text('yaml') 带注释头，且可被 yaml.safe_load 回读"""
    from jenkins_config.config_io import PLACEHOLDER_VALUES, template_text

    text = template_text("yaml")
    data = yaml.safe_load(text)

    assert text.lstrip().startswith("#")
    assert data["server"]["url"] == PLACEHOLDER_VALUES["server.url"]
    assert data["server"]["token"] == PLACEHOLDER_VALUES["server.token"]
    assert set(data["environments"]) == {"dev", "prod"}


def test_template_text_json_round_trips():
    """template_text('json') 不带注释，可被 json.loads 回读"""
    import json

    from jenkins_config.config_io import generate_template, template_text

    assert json.loads(template_text("json")) == generate_template()


def test_template_text_rejects_unknown_format():
    """非法 fmt 抛 ValueError（由 MCP 侧折算为结构化载荷）"""
    from jenkins_config.config_io import template_text

    with pytest.raises(ValueError, match="不支持的模板格式"):
        template_text("toml")


def test_template_text_never_reads_example_files(monkeypatch):
    """模板内容来自 dict 常量，绝不读源码树里的示例文件

    npx / 单文件 EXE 形态下没有源码树，读示例文件会在真实部署时直接报错。
    这里把 Path.read_text 打成炸弹，一旦有人改成"读文件"实现立即失败。
    """
    from jenkins_config.config_io import template_text

    def _boom(*args, **kwargs):
        """任何文件读取都视为违反约束"""
        raise AssertionError("template_text 不应读取任何文件")

    monkeypatch.setattr(Path, "read_text", _boom)
    monkeypatch.setattr(Path, "read_bytes", _boom)

    assert template_text("yaml")
    assert template_text("json")


def test_template_text_output_is_loadable_as_config(tmp_path):
    """生成的 YAML 文本落盘后能被 Config.load 直接加载"""
    from jenkins_config.config_io import template_text

    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text(template_text("yaml"), encoding="utf-8")

    config = Config.load(str(config_file))

    assert [name for name, _ in config.list_environments()] == ["dev", "prod"]
