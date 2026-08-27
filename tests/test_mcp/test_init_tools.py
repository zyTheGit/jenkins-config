# tests/test_mcp/test_init_tools.py
"""
init_config（零配置初始化）测试

测试覆盖：
- 分级门控：不存在即创建（免门控）、已存在拒绝覆盖、overwrite 需门控
- 目标枚举与格式校验一律回结构化载荷
- 家目录不可用、CWD 过宽这两条"必须给出路"的失败分支
- 生成文件的凭据字段恒为占位符
- T-18 端到端：init_config → 填真实值 → list_environments 成功

所有用例都把家目录改到 tmp_path：默认 target='user' 会真的落盘，
绝不能写进开发者真实的 ~/.jenkins-config。
"""

from pathlib import Path

import pytest
import yaml

from jenkins_config.config_io import PLACEHOLDER_VALUES
from jenkins_config.mcp.tools.init_tools import DEFAULT_CONFIG_FILE_NAME, init_config
from jenkins_config.mcp.utils import CONFIG_ROOTS_ENV_VAR, WRITE_ENV_VAR
from jenkins_config.paths import APP_DIR_NAME, CONFIG_ENV_VAR

SENTINEL = "server:\n  url: http://keep-me:8080\n  token: keep-me-token\n"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把家目录指向 tmp_path 下的独立目录

    Args:
        tmp_path: pytest 临时目录
        monkeypatch: pytest 猴子补丁夹具

    Returns:
        伪家目录 Path
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    return home


def _user_config_file(home: Path) -> Path:
    """伪家目录下的目标配置文件路径

    Args:
        home: 伪家目录

    Returns:
        ~/.jenkins-config/jenkins-config.yaml 对应的 Path
    """
    return home / APP_DIR_NAME / DEFAULT_CONFIG_FILE_NAME


def _seed_existing(home: Path) -> Path:
    """在目标位置预置一份"用户已有配置"

    Args:
        home: 伪家目录

    Returns:
        已写入哨兵内容的配置文件路径
    """
    target = _user_config_file(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SENTINEL, encoding="utf-8")
    return target


# ============================================================================
# 创建路径（免门控）
# ============================================================================


def test_creates_template_without_write_gate(fake_home, monkeypatch):
    """目标不存在时直接创建，且不要求 JENKINS_MCP_ALLOW_WRITE"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)

    result = init_config()

    assert result["created"] is True
    assert result["path"] == str(_user_config_file(fake_home))
    assert Path(result["path"]).is_file()
    assert result["backup"] == ""
    assert result["next_steps"]


def test_created_file_keeps_credentials_as_placeholders(fake_home, monkeypatch):
    """生成文件的凭据字段必须恒等于 PLACEHOLDER_VALUES（不得猜填真实值）"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)
    monkeypatch.setenv("JENKINS_TOKEN", "leak-me")

    result = init_config()
    data = yaml.safe_load(Path(result["path"]).read_text(encoding="utf-8"))

    assert data["server"]["url"] == PLACEHOLDER_VALUES["server.url"]
    assert data["server"]["token"] == PLACEHOLDER_VALUES["server.token"]
    assert "leak-me" not in Path(result["path"]).read_text(encoding="utf-8")


def test_returns_template_fields_with_required_keys(fake_home):
    """返回体带字段清单，必填项覆盖 server.url / server.token / environments"""
    result = init_config()

    required = {
        item["key"] for item in result["template_fields"] if item["required"]
    }

    assert {"server.url", "server.token", "environments"} <= required


# ============================================================================
# 已存在 / 覆盖门控
# ============================================================================


def test_existing_target_is_never_touched_by_default(fake_home, monkeypatch):
    """目标已存在且 overwrite=false 时拒绝，且原文件内容未被修改"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)
    target = _seed_existing(fake_home)

    result = init_config()

    assert result["error_code"] == "config_exists"
    assert result["created"] is False
    assert target.read_text(encoding="utf-8") == SENTINEL
    assert not (target.parent / f"{target.name}.bak").exists()


def test_config_exists_is_not_waived_by_write_gate(fake_home, monkeypatch):
    """已存在 + overwrite=false 与写门控状态无关（产品硬约束）"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")
    target = _seed_existing(fake_home)

    result = init_config()

    assert result["error_code"] == "config_exists"
    assert target.read_text(encoding="utf-8") == SENTINEL


def test_config_exists_next_steps_offer_both_ways_out(fake_home, monkeypatch):
    """config_exists 必须同时给出「查看现有配置」与「显式传 overwrite=true」"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)
    _seed_existing(fake_home)

    steps = init_config()["next_steps"]

    assert any("where_config" in step for step in steps)
    assert any("overwrite=true" in step for step in steps)


def test_overwrite_requires_write_gate(fake_home, monkeypatch):
    """overwrite=true 但未开门控时拒绝，原文件保持不变"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)
    target = _seed_existing(fake_home)

    result = init_config(overwrite=True)

    assert result["error_code"] == "write_not_allowed"
    assert result["created"] is False
    assert target.read_text(encoding="utf-8") == SENTINEL


def test_overwrite_with_gate_writes_backup(fake_home, monkeypatch):
    """overwrite=true 且门控已开时写入成功，且 .bak 内容等于原文件"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")
    target = _seed_existing(fake_home)

    result = init_config(overwrite=True)

    backup = Path(result["backup"])
    assert result["created"] is True
    assert backup.read_text(encoding="utf-8") == SENTINEL
    assert target.read_text(encoding="utf-8") != SENTINEL
    assert PLACEHOLDER_VALUES["server.url"] in target.read_text(encoding="utf-8")


# ============================================================================
# 入参与环境的失败分支
# ============================================================================


def test_invalid_target_is_rejected(fake_home):
    """非枚举 target（含任意路径）一律 invalid_target，不写任何文件"""
    result = init_config(target="/etc")

    assert result["error_code"] == "invalid_target"
    assert result["created"] is False
    assert not _user_config_file(fake_home).exists()


def test_invalid_format_returns_structured_payload(fake_home):
    """非法 format 回结构化载荷，而不是抛裸异常"""
    result = init_config(format="toml")

    assert result["created"] is False
    assert result["error_code"]
    assert result["next_steps"]
    assert not _user_config_file(fake_home).exists()


def test_home_unavailable_does_not_crash(monkeypatch):
    """家目录不可解析时回 home_unavailable，并给出两条出路"""
    def _no_home():
        """模拟 HOME / USERPROFILE 均缺失"""
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", staticmethod(_no_home))
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)

    result = init_config(target="user")

    assert result["error_code"] == "home_unavailable"
    assert result["created"] is False
    assert any("target='cwd'" in step for step in result["next_steps"])
    assert any(CONFIG_ENV_VAR in step for step in result["next_steps"])


def test_cwd_too_broad_is_denied_with_ways_out(monkeypatch):
    """CWD 为盘符根（判过宽）时拒绝，并给出 target='user' 与 CONFIG_ROOTS 两条出路"""
    monkeypatch.chdir(Path(Path.cwd().anchor))
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)

    result = init_config(target="cwd")

    assert result["error_code"] == "config_path_denied"
    assert result["created"] is False
    assert any("target='user'" in step for step in result["next_steps"])
    assert any(CONFIG_ROOTS_ENV_VAR in step for step in result["next_steps"])


def test_cwd_target_writes_into_working_directory(tmp_path, monkeypatch, fake_home):
    """target='cwd' 在白名单内的工作目录下正常生成配置"""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    result = init_config(target="cwd")

    assert result["created"] is True
    assert Path(result["path"]) == workdir / DEFAULT_CONFIG_FILE_NAME


# ============================================================================
# T-18 端到端：从零到 list_environments 成功
# ============================================================================


def test_end_to_end_init_then_list_environments(tmp_path, monkeypatch, fake_home):
    """无项目目录假设下：init_config → 填真实值 → list_environments 返回真实环境

    刻意把项目根与 CWD 都指到空目录：npx / EXE 用户手上没有项目目录，
    这条链路必须只靠用户级配置目录就能走通。
    """
    from jenkins_config import paths
    from jenkins_config.mcp.tools.config_tools import list_environments

    empty_project = tmp_path / "no_project"
    empty_project.mkdir()
    workdir = tmp_path / "elsewhere"
    workdir.mkdir()
    monkeypatch.setattr(paths, "project_root", lambda: empty_project)
    monkeypatch.chdir(workdir)

    result = init_config()
    created = Path(result["path"])
    raw = created.read_text(encoding="utf-8")
    raw = raw.replace(PLACEHOLDER_VALUES["server.url"], "http://jenkins.internal:8080")
    raw = raw.replace(PLACEHOLDER_VALUES["server.token"], "real-api-token")
    created.write_text(raw, encoding="utf-8")

    environments = list_environments()

    assert [item["name"] for item in environments] == ["dev", "prod"]
    assert all("error_code" not in item for item in environments)
