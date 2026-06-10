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
