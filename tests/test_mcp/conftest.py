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

