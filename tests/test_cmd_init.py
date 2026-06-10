# tests/test_cmd_init.py
"""
初始化配置命令模块测试

测试覆盖：
- run_init 静默模式（复制示例 / 生成模板）
- run_init 已存在时的行为
- _cli_cmd 方法
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

from jenkins_config.cmd_init import run_init, _cli_cmd


# ============================================================================
# _cli_cmd
# ============================================================================


def test_cli_cmd_source():
    """源码模式返回 uv run 命令"""
    with patch("jenkins_config.cmd_init.sys.frozen", False, create=True):
        cmd = _cli_cmd()
        assert "uv run python -m jenkins_config.cli" == cmd


def test_cli_cmd_frozen():
    """EXE 模式返回可执行文件名"""
    with (
        patch("jenkins_config.cmd_init.sys.frozen", True, create=True),
        patch("jenkins_config.cmd_init.sys.executable", "/usr/bin/jenkins-build.exe"),
    ):
        cmd = _cli_cmd()
        assert cmd == "jenkins-build.exe"


# ============================================================================
# run_init - 静默模式（非交互）
# ============================================================================


def test_run_init_with_example_yaml(tmp_path):
    """有示例 YAML 时复制示例文件"""
    config_file = tmp_path / "jenkins-config.yaml"
    example_yaml = tmp_path / "jenkins-config.example.yaml"
    example_yaml.write_text("server:\n  url: http://localhost:8080\n",
                            encoding="utf-8")

    args = MagicMock()
    args.interactive = False
    args.force = False

    run_init(config_file, args)

    assert config_file.exists()
    content = config_file.read_text(encoding="utf-8")
    assert "http://localhost:8080" in content


def test_run_init_with_example_json(tmp_path):
    """有示例 JSON（无 YAML）时复制示例 JSON"""
    config_file = tmp_path / "jenkins-config.yaml"
    example_json = tmp_path / "jenkins-config.example.json"
    example_json.write_text('{"server": {"url": "http://localhost:8080"}}',
                            encoding="utf-8")

    args = MagicMock()
    args.interactive = False
    args.force = False

    run_init(config_file, args)

    assert config_file.exists()
    content = config_file.read_text(encoding="utf-8")
    assert "http://localhost:8080" in content


def test_run_init_generate_template(tmp_path):
    """无示例文件时生成模板"""
    config_file = tmp_path / "jenkins-config.yaml"

    args = MagicMock()
    args.interactive = False
    args.force = False

    run_init(config_file, args)

    assert config_file.exists()
    content = config_file.read_text(encoding="utf-8")
    assert "server" in content
    assert "url" in content
    assert "environments" in content


def test_run_init_file_exists_no_force(tmp_path):
    """配置文件已存在且无 --force 时提示"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("existing content", encoding="utf-8")
    example_yaml = tmp_path / "jenkins-config.example.yaml"
    example_yaml.write_text("server:\n  url: http://new.example.com\n",
                            encoding="utf-8")

    args = MagicMock()
    args.interactive = False
    args.force = False

    with patch("builtins.input", return_value="n"):
        run_init(config_file, args)

    # 文件未被覆盖
    assert config_file.read_text(encoding="utf-8") == "existing content"


def test_run_init_file_exists_with_force(tmp_path):
    """配置文件已存在且有 --force 时覆盖"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text("old content", encoding="utf-8")
    example_yaml = tmp_path / "jenkins-config.example.yaml"
    example_yaml.write_text("server:\n  url: http://new.example.com\n",
                            encoding="utf-8")

    args = MagicMock()
    args.interactive = False
    args.force = True

    run_init(config_file, args)

    content = config_file.read_text(encoding="utf-8")
    assert "http://new.example.com" in content
    assert "old content" not in content


def test_run_init_with_interactive_dispatches(tmp_path):
    """交互模式调用 _run_init_interactive"""
    config_file = tmp_path / "jenkins-config.yaml"

    args = MagicMock()
    args.interactive = True
    args.force = False

    with patch("jenkins_config.cmd_init._run_init_interactive") as mock_interactive:
        run_init(config_file, args)
        mock_interactive.assert_called_once_with(config_file)
