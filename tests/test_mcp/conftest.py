# tests/test_mcp/conftest.py
"""
MCP 测试目录公共配置

tests/test_mcp/ 下的测试依赖 mcp 包（pyproject.toml 中的可选依赖 extra "mcp"）。
在未安装 mcp 的环境中（如仅执行 `uv sync --extra dev`），
通过 importorskip 优雅跳过整个目录，而非报 ModuleNotFoundError。
"""

import pytest

pytest.importorskip("mcp")

from jenkins_config.mcp.utils import CONFIG_ROOTS_ENV_VAR  # noqa: E402


@pytest.fixture(autouse=True)
def allow_tmp_config_roots(monkeypatch, tmp_path_factory):
    """把 pytest 临时目录加入 MCP 配置路径白名单

    resolve_config_path 只接受 allowed_config_bases() 之内的配置文件，
    而测试用的配置都写在 tmp_path 下，需要显式放行这个根目录。
    """
    monkeypatch.setenv(
        CONFIG_ROOTS_ENV_VAR, str(tmp_path_factory.getbasetemp())
    )


@pytest.fixture
def usable_config(tmp_path):
    """写一份填写完整的配置文件，但不生成任何历史文件

    放在 conftest 里共享：多个测试文件都需要"配置可用"这个前提，
    各自复制一份 YAML 会让"什么算完整配置"出现两份定义。

    Returns:
        配置文件的绝对路径字符串

    Example:
        >>> usable_config  # doctest: +SKIP
        '/tmp/pytest-xxx/jenkins-config.yaml'
    """
    config = tmp_path / "jenkins-config.yaml"
    config.write_text(
        "server:\n"
        "  url: http://jenkins.example.com\n"
        "  username: admin\n"
        "  token: real-token-value\n"
        "environments:\n"
        "  dev:\n"
        "    description: 开发环境\n"
        "    projects:\n"
        "      - name: project-a\n",
        encoding="utf-8",
    )
    return str(config)

