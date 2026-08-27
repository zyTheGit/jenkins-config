# tests/test_mcp/test_config_tools.py
"""
MCP 配置查询工具测试

测试覆盖：
- list_environments 返回正确格式
- list_projects 按环境过滤
- show_config token 脱敏
- 配置加载失败的错误处理
- save_config 的写开关、格式校验与备份
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jenkins_config.config_types import BuildConfig, ServerConfig
from jenkins_config.mcp.utils import WRITE_ENV_VAR


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_config():
    """创建 mock Config 实例"""
    config = Mock()  # 不使用 spec，因为 Config 的方法是猴子补丁添加的
    config.server = ServerConfig(
        url="http://jenkins:8080",
        username="admin",
        token="abcdef123456"
    )
    config.build = BuildConfig(
        mode="parallel",
        poll_interval=10,
        queue_timeout=30,
        build_timeout=3600,
        curl_timeout=30,
        log_dir="./jenkins_logs",
        log_retention_days=3,
        max_parallel=5,
    )
    config.list_environments.return_value = [
        ("dev", "开发环境"),
        ("test", "测试环境"),
        ("sit", "SIT 环境"),
    ]
    config.list_projects.return_value = [
        ("dev", "project-a", "project-a"),
        ("dev", "project-b", "folder/project-b"),
    ]
    return config


# ============================================================================
# list_environments 测试
# ============================================================================


def test_list_environments_returns_correct_format(mock_config):
    """验证 list_environments 返回正确格式的环境列表"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.config_tools import list_environments

        result = list_environments()

        assert len(result) == 3
        assert result[0] == {"name": "dev", "description": "开发环境"}
        assert result[1] == {"name": "test", "description": "测试环境"}
        assert result[2] == {"name": "sit", "description": "SIT 环境"}


def test_list_environments_empty_config():
    """验证空配置时 list_environments 返回空列表"""
    empty_config = Mock()  # 不使用 spec
    empty_config.list_environments.return_value = []

    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=empty_config):
        from jenkins_config.mcp.tools.config_tools import list_environments

        result = list_environments()
        assert result == []


def test_list_environments_config_load_error():
    """验证配置加载失败时返回单元素纯错误载荷（不含伪造的业务键）"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", side_effect=FileNotFoundError("配置文件不存在")):
        from jenkins_config.mcp.tools.config_tools import list_environments

        result = list_environments()

        assert len(result) == 1
        payload = result[0]
        assert set(payload) == {"error_code", "error", "config_path", "next_steps", "docs"}
        # 旧实现塞 name="error"，模型会把它当成一个真实环境
        assert "name" not in payload
        assert payload["error_code"] == "config_not_found"
        assert "加载配置失败" in payload["error"]
        assert payload["next_steps"]


# ============================================================================
# list_projects 测试
# ============================================================================


def test_list_projects_returns_correct_format(mock_config):
    """验证 list_projects 返回正确格式的项目列表"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.config_tools import list_projects

        result = list_projects(env="dev")

        assert len(result) == 2
        assert result[0] == {"environment": "dev", "name": "project-a", "path": "project-a"}
        assert result[1] == {"environment": "dev", "name": "project-b", "path": "folder/project-b"}


def test_list_projects_filters_by_env(mock_config):
    """验证 list_projects 按环境过滤"""
    mock_config.list_projects.return_value = [
        ("test", "project-c", "project-c"),
    ]

    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.config_tools import list_projects

        result = list_projects(env="test")

        mock_config.list_projects.assert_called_with("test")
        assert len(result) == 1
        assert result[0]["environment"] == "test"


def test_list_projects_all_envs_when_env_empty(mock_config):
    """验证 env 为空时列出所有环境的项目"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.config_tools import list_projects

        list_projects(env="")

        # env 为空时应该传 None
        mock_config.list_projects.assert_called_with(None)


def test_list_projects_config_load_error():
    """验证配置加载失败时 list_projects 返回单元素纯错误载荷"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", side_effect=Exception("配置错误")):
        from jenkins_config.mcp.tools.config_tools import list_projects

        result = list_projects(env="dev")

        assert len(result) == 1
        payload = result[0]
        assert set(payload) == {"error_code", "error", "config_path", "next_steps", "docs"}
        # 旧实现把错误塞进 path 字段，调用方会拿它当 Job 路径去触发构建
        assert "path" not in payload and "environment" not in payload
        assert "加载配置失败" in payload["error"]
        assert payload["next_steps"]


# ============================================================================
# show_config 测试
# ============================================================================


def test_show_config_token_masking(mock_config):
    """验证 show_config 对 token 完全脱敏"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.config_tools import show_config

        result = show_config()

        # token 完全脱敏，不保留任何原始字符，仅标注长度
        assert result["token"] == "*** (长度 12)"
        assert "abcdef123456" not in result["token"]
        assert "abcd" not in result["token"]


def test_show_config_short_token_masking():
    """验证短 token 同样完全脱敏"""
    config = Mock()  # 不使用 spec
    config.server = ServerConfig(url="http://localhost", username="admin", token="abc")
    config.build = BuildConfig()
    config.list_environments.return_value = []

    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=config):
        from jenkins_config.mcp.tools.config_tools import show_config

        result = show_config()
        assert result["token"] == "*** (长度 3)"


def test_show_config_returns_complete_info(mock_config):
    """验证 show_config 返回完整的配置摘要"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.config_tools import show_config

        result = show_config()

        assert result["server_url"] == "http://jenkins:8080"
        assert result["username"] == "admin"
        assert len(result["environments"]) == 3
        assert result["build_config"]["mode"] == "parallel"
        assert result["build_config"]["poll_interval"] == 10
        assert result["build_config"]["max_parallel"] == 5


def test_show_config_error_handling():
    """验证 show_config 配置加载失败时返回错误"""
    with patch("jenkins_config.mcp.tools.config_tools.get_config", side_effect=Exception("连接失败")):
        from jenkins_config.mcp.tools.config_tools import show_config

        result = show_config()

        assert "error" in result
        assert "加载配置失败" in result["error"]


# ============================================================================
# save_config 测试
# ============================================================================


def test_save_config_success(mock_config, tmp_path, monkeypatch):
    """验证 save_config 成功保存配置并生成备份"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")
    target = tmp_path / "config.yaml"
    target.write_text("old: 1", encoding="utf-8")

    with patch(
        "jenkins_config.mcp.tools.config_tools.resolve_config_path",
        return_value=str(target),
    ), patch("jenkins_config.mcp.tools.config_tools.get_config", return_value=mock_config):
        from jenkins_config.mcp.tools.config_tools import save_config

        result = save_config()

        assert result["message"] == "配置已保存"
        assert result["path"] == str(target)
        assert Path(result["backup"]).read_text(encoding="utf-8") == "old: 1"
        mock_config.save.assert_called_once_with(str(target))


def test_save_config_denied_without_write_env(monkeypatch):
    """验证未开启写开关时 save_config 直接拒绝且不加载配置"""
    monkeypatch.delenv(WRITE_ENV_VAR, raising=False)

    with patch("jenkins_config.mcp.tools.config_tools.get_config") as mock_get_config:
        from jenkins_config.mcp.tools.config_tools import save_config

        result = save_config()

        assert WRITE_ENV_VAR in result["error"]
        mock_get_config.assert_not_called()


def test_save_config_error(monkeypatch):
    """验证 save_config 保存失败时返回错误"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")

    with patch(
        "jenkins_config.mcp.tools.config_tools.resolve_config_path",
        return_value="/path/config.yaml",
    ), patch(
        "jenkins_config.mcp.tools.config_tools.get_config",
        side_effect=Exception("权限不足"),
    ):
        from jenkins_config.mcp.tools.config_tools import save_config

        result = save_config()

        assert "error" in result
        assert "保存配置失败" in result["error"]


def test_save_config_rejects_json_path(monkeypatch):
    """验证 save_config 拒绝非 YAML 路径，不写入文件"""
    monkeypatch.setenv(WRITE_ENV_VAR, "1")

    with patch(
        "jenkins_config.mcp.tools.config_tools.resolve_config_path",
        return_value="/path/jenkins-config.json",
    ), patch("jenkins_config.mcp.tools.config_tools.get_config") as mock_get_config:
        from jenkins_config.mcp.tools.config_tools import save_config

        result = save_config()

        assert "error" in result
        assert "仅支持保存为 YAML 格式" in result["error"]
        # 不应尝试加载/保存配置
        mock_get_config.assert_not_called()


# ============================================================================
# resolve_config_path 锚定规则测试
# ============================================================================


def test_host_allowed_env_var_is_authoritative(monkeypatch):
    """验证 ALLOWED_HOSTS 非空时不再叠加配置文件主机（防 CWD 配置扩大白名单）"""
    from jenkins_config.mcp.utils import ALLOWED_HOSTS_ENV_VAR, host_allowed

    monkeypatch.setenv(ALLOWED_HOSTS_ENV_VAR, "jenkins.internal")
    with patch(
        "jenkins_config.mcp.utils.trusted_server_url",
        return_value="http://cwd-planted.example.com",
    ):
        assert host_allowed("http://jenkins.internal") is True
        assert host_allowed("http://cwd-planted.example.com") is False


def test_resolve_config_path_allows_whitelisted_explicit_path(tmp_path):

    """验证白名单根目录内的显式 config_path 可用（tmp_path 由 conftest 放行）"""
    from jenkins_config.mcp.utils import resolve_config_path

    target = tmp_path / "config.yaml"
    assert resolve_config_path(str(target)) == str(target.resolve())


def test_resolve_config_path_rejects_path_outside_allowed_roots(tmp_path, monkeypatch):
    """验证白名单之外的显式 config_path 被拒绝，避免绕过主机白名单/覆写任意文件"""
    from jenkins_config.mcp.utils import CONFIG_ROOTS_ENV_VAR, resolve_config_path

    # 只放行 tmp_path/allowed，另一个同级目录应被拒绝
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv(CONFIG_ROOTS_ENV_VAR, str(allowed))

    with pytest.raises(PermissionError, match="不在允许范围内"):
        resolve_config_path(str(tmp_path / "outside" / "config.yaml"))


def test_resolve_config_path_source_mode_anchors_to_project_root(tmp_path, monkeypatch):
    """验证源码模式下先锚定项目根目录（与 CLI 对齐），即使 CWD 无配置也能找到"""
    from jenkins_config import paths
    from jenkins_config.mcp import utils

    project_root = tmp_path / "repo"
    (project_root / "jenkins_config").mkdir(parents=True)
    expected = project_root / "jenkins-config.yaml"
    expected.write_text("server: {}", encoding="utf-8")

    # 用假的模块文件路径模拟“源码模式下 paths.py 的上一级即项目根”
    monkeypatch.setattr(paths, "__file__", str(project_root / "jenkins_config" / "paths.py"))

    empty_cwd = tmp_path / "elsewhere"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    assert utils.resolve_config_path() == str(expected.resolve())


def test_resolve_config_path_returns_default_when_missing(tmp_path, monkeypatch):
    """验证所有候选路径都不存在时返回项目根目录下的默认文件名"""
    from jenkins_config import paths
    from jenkins_config.mcp import utils

    root = tmp_path / "repo"
    (root / "jenkins_config").mkdir(parents=True)
    monkeypatch.setattr(paths, "__file__", str(root / "jenkins_config" / "paths.py"))
    monkeypatch.chdir(tmp_path)

    assert utils.resolve_config_path() == str(root / "jenkins-config.yaml")


def test_resolve_config_path_detects_yml_suffix(tmp_path, monkeypatch):
    """验证 .yml 后缀同样被探测（与 CLI 的候选名单一致）"""
    from jenkins_config import paths
    from jenkins_config.mcp import utils

    root = tmp_path / "repo"
    (root / "jenkins_config").mkdir(parents=True)
    expected = root / "jenkins-config.yml"
    expected.write_text("server: {}", encoding="utf-8")
    monkeypatch.setattr(paths, "__file__", str(root / "jenkins_config" / "paths.py"))
    monkeypatch.chdir(tmp_path)

    assert utils.resolve_config_path() == str(expected.resolve())


def test_resolve_config_path_frozen_mode_prefers_cwd(tmp_path, monkeypatch):
    """验证冻结（EXE）模式下优先探测进程 CWD，而非项目根目录"""
    import sys

    from jenkins_config.mcp.utils import resolve_config_path

    cwd_config = tmp_path / "jenkins-config.json"
    cwd_config.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.chdir(tmp_path)
    result = resolve_config_path()

    # CWD 下存在配置时应优先返回 CWD 路径，而非项目根的 yaml
    assert result == str(cwd_config.resolve())


def test_resolve_history_path_anchors_to_config_dir(tmp_path):
    """验证历史文件路径锚定到配置文件同级 data 目录"""
    from jenkins_config.mcp.utils import resolve_history_path

    config_file = tmp_path / "jenkins-config.yaml"
    assert resolve_history_path(str(config_file)) == tmp_path / "data" / "build_history.json"


def test_resolve_config_path_prefers_env_var(tmp_path, monkeypatch):
    """验证 JENKINS_MCP_CONFIG 优先于候选目录探测（stdio 下 CWD 不可控）"""
    from jenkins_config import paths
    from jenkins_config.mcp import utils

    # CWD 里放一份配置，环境变量指向另一处，应取环境变量那份
    cwd_config = tmp_path / "cwd"
    cwd_config.mkdir()
    (cwd_config / "jenkins-config.yaml").write_text("server: {}", encoding="utf-8")
    monkeypatch.chdir(cwd_config)

    env_config = tmp_path / "elsewhere" / "jenkins-config.yaml"
    env_config.parent.mkdir()
    env_config.write_text("server: {}", encoding="utf-8")
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(env_config))

    assert utils.resolve_config_path() == str(env_config.resolve())


def test_env_config_file_is_allowed_exactly(tmp_path, monkeypatch):
    """验证环境变量指定的文件精确放行，但其父目录不整树进入白名单"""
    from jenkins_config import paths
    from jenkins_config.mcp import utils

    env_config = tmp_path / "deploy" / "jenkins-config.yaml"
    env_config.parent.mkdir()
    env_config.write_text("server: {}", encoding="utf-8")
    sibling = env_config.parent / "secret.yaml"
    sibling.write_text("server: {}", encoding="utf-8")
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(env_config))
    # 覆盖 conftest 放行整个 basetemp 的默认白名单，否则同目录文件也会被放行
    monkeypatch.setenv(utils.CONFIG_ROOTS_ENV_VAR, str(tmp_path / "nowhere"))

    # 环境变量那一个文件放行
    assert utils.resolve_config_path() == str(env_config.resolve())
    # 同目录下的其他文件不放行
    assert env_config.parent.resolve() not in utils.allowed_config_bases()
    with pytest.raises(PermissionError, match="不在允许范围内"):
        utils.resolve_config_path(str(sibling))


def test_env_config_does_not_affect_cli_probe(tmp_path, monkeypatch):
    """验证 JENKINS_MCP_CONFIG 不影响 CLI 的自动探测（否则导出该变量会改掉 CLI 行为）"""
    from jenkins_config import paths

    root = tmp_path / "repo"
    (root / "jenkins_config").mkdir(parents=True)
    project_config = root / "jenkins-config.yaml"
    project_config.write_text("server: {}", encoding="utf-8")
    monkeypatch.setattr(paths, "__file__", str(root / "jenkins_config" / "paths.py"))
    monkeypatch.chdir(tmp_path)

    env_config = tmp_path / "elsewhere" / "jenkins-config.yaml"
    env_config.parent.mkdir()
    env_config.write_text("server: {}", encoding="utf-8")
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, str(env_config))

    assert paths.resolve_config_file() == project_config


def test_env_config_file_rejects_relative_value(tmp_path, monkeypatch):
    """验证相对路径的 JENKINS_MCP_CONFIG 被忽略——相对值仍受 CWD 影响，失去确定性"""
    from jenkins_config import paths

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(paths.CONFIG_ENV_VAR, "jenkins-config.yaml")

    assert paths.env_config_file() is None


def test_search_bases_includes_user_config_dir():
    """验证候选目录末位是用户级配置目录（MCP 部署的稳定落点）"""
    from jenkins_config.paths import search_bases, user_config_dir

    assert search_bases()[-1] == user_config_dir()


def test_cli_resolve_config_expands_user_home():
    """验证 CLI 的 -c 参数与 MCP 侧一致地展开 ~（两侧共用 paths.resolve_config_file）"""
    from pathlib import Path

    from jenkins_config.cli import _resolve_config

    result = _resolve_config("~/jenkins-config.yaml")

    assert result == Path.home() / "jenkins-config.yaml"


def test_resolve_config_path_falls_back_to_user_config_dir(tmp_path, monkeypatch):
    """验证项目根与 CWD 都没有配置时，能从用户级配置目录找到"""
    from jenkins_config import paths
    from jenkins_config.mcp import utils

    root = tmp_path / "repo"
    (root / "jenkins_config").mkdir(parents=True)
    monkeypatch.setattr(paths, "__file__", str(root / "jenkins_config" / "paths.py"))

    empty_cwd = tmp_path / "elsewhere"
    empty_cwd.mkdir()
    monkeypatch.chdir(empty_cwd)

    user_dir = tmp_path / "userconfig"
    user_dir.mkdir()
    expected = user_dir / "jenkins-config.yaml"
    expected.write_text("server: {}", encoding="utf-8")
    monkeypatch.setattr(paths, "user_config_dir", lambda: user_dir)

    assert utils.resolve_config_path() == str(expected.resolve())


def test_resolve_history_path_anchors_beside_config(tmp_path):
    """验证历史文件始终锚定在配置文件同级的 data/ 下

    用户级目录三平台统一为 `~/.jenkins-config`，配置与数据同处一地，
    不再按平台把历史改锚到别的目录。
    """
    from jenkins_config import paths

    config_file = tmp_path / "userconfig" / "jenkins-config.yaml"

    result = paths.resolve_history_path(str(config_file))

    assert result == tmp_path / "userconfig" / "data" / "build_history.json"


def test_user_config_dir_is_unified_under_home():
    """验证用户级配置目录三平台统一为 ~/.jenkins-config，日志为其子目录"""
    from jenkins_config import paths

    assert paths.user_config_dir() == Path.home() / ".jenkins-config"
    assert paths.user_log_dir() == paths.user_config_dir() / "logs"


def test_allowed_config_bases_drops_overly_broad_roots(tmp_path, monkeypatch):
    """验证文件系统根与家目录不进入配置白名单

    stdio 拉起时 CWD 不可控，若把 `/` 或家目录当作允许根目录，
    _is_within 对任意路径都成立，等于整个白名单全放行。
    """
    from jenkins_config import paths
    from jenkins_config.mcp import utils

    fs_root = Path(tmp_path.anchor)
    home = Path.home().resolve()
    monkeypatch.setattr(paths, "search_bases", lambda: [fs_root, home, tmp_path])
    monkeypatch.setattr(utils, "search_bases", lambda: [fs_root, home, tmp_path])
    monkeypatch.delenv(utils.CONFIG_ROOTS_ENV_VAR, raising=False)

    bases = utils.allowed_config_bases()

    assert bases == [tmp_path.resolve()]


def test_config_roots_env_var_is_not_filtered(tmp_path, monkeypatch):
    """验证 JENKINS_MCP_CONFIG_ROOTS 是部署方显式设定，不参与"过宽"过滤"""
    from jenkins_config.mcp import utils

    home = Path.home().resolve()
    monkeypatch.setattr(utils, "search_bases", lambda: [tmp_path])
    monkeypatch.setenv(utils.CONFIG_ROOTS_ENV_VAR, str(home))

    assert home in utils.allowed_config_bases()
