# tests/test_cli.py
"""
CLI 入口模块测试

测试覆盖：
- _resolve_config_path 路径解析
- main 函数参数分发
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from jenkins_config.cli import _resolve_config_path, main


# ============================================================================
# _resolve_config_path
# ============================================================================


def test_resolve_absolute_path(tmp_path):
    """绝对路径直接返回"""
    abs_path = str(tmp_path / "config.yaml")
    result = _resolve_config_path(abs_path)
    assert result == Path(abs_path)


def test_resolve_relative_source(tmp_path):
    """源码模式相对路径解析到项目根目录"""
    fake_cli = str(tmp_path / "jenkins_config" / "cli.py")
    with (
        patch("jenkins_config.cli.sys.frozen", False, create=True),
        patch("jenkins_config.cli.__file__", fake_cli),
    ):
        result = _resolve_config_path("jenkins-config.yaml")
        expected = tmp_path / "jenkins-config.yaml"
        assert result == expected


def test_resolve_relative_frozen_cwd_exists(tmp_path):
    """EXE 模式：当前目录有配置文件则用当前目录"""
    config_arg = "jenkins-config.yaml"
    config_in_cwd = tmp_path / config_arg
    config_in_cwd.write_text("dummy", encoding="utf-8")

    with (
        patch("jenkins_config.cli.sys.frozen", True, create=True),
        patch("jenkins_config.cli.Path.cwd", return_value=tmp_path),
        patch("jenkins_config.cli.sys.executable",
              str(tmp_path / "dist" / "jenkins-build.exe")),
    ):
        result = _resolve_config_path(config_arg)
        assert result == config_in_cwd


def test_resolve_relative_frozen_cwd_missing(tmp_path):
    """EXE 模式：当前目录无配置，exe 目录有则用 exe 目录"""
    config_arg = "jenkins-config.yaml"
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir(parents=True)
    config_in_exe = exe_dir / config_arg
    config_in_exe.write_text("dummy", encoding="utf-8")

    with (
        patch("jenkins_config.cli.sys.frozen", True, create=True),
        patch("jenkins_config.cli.Path.cwd", return_value=tmp_path),
        patch("jenkins_config.cli.sys.executable",
              str(exe_dir / "jenkins-build.exe")),
    ):
        result = _resolve_config_path(config_arg)
        assert result == config_in_exe


def test_resolve_relative_frozen_both_missing(tmp_path):
    """EXE 模式：两处都无配置，回退到当前目录"""
    config_arg = "jenkins-config.yaml"
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir(parents=True)

    with (
        patch("jenkins_config.cli.sys.frozen", True, create=True),
        patch("jenkins_config.cli.Path.cwd", return_value=tmp_path),
        patch("jenkins_config.cli.sys.executable",
              str(exe_dir / "jenkins-build.exe")),
    ):
        result = _resolve_config_path(config_arg)
        assert result == tmp_path / config_arg


# ============================================================================
# main - 参数分发
# ============================================================================
# main() 使用延迟导入（函数内部 from .cmd_list import ...），
# 所以 patch 目标要在函数的源模块，而不是 cli 模块


def test_main_help_config(capsys):
    """--help-config 显示配置模板"""
    test_args = ["prog", "--help-config"]
    with patch.object(sys, "argv", test_args):
        main()
    captured = capsys.readouterr()
    assert "server" in captured.out
    assert "url" in captured.out


def test_main_list_envs():
    """--list-envs 分发到 list_environments"""
    test_args = ["prog", "--list-envs"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_list.list_environments") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_list_projects():
    """--list-projects 分发到 list_projects"""
    test_args = ["prog", "--list-projects"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_list.list_projects") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_list_projects_with_env():
    """--list-projects test 带环境参数"""
    test_args = ["prog", "--list-projects", "test"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_list.list_projects") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()
        args = mock_fn.call_args[0]
        assert args[1] == "test"


def test_main_history():
    """--history 分发到 show_history"""
    test_args = ["prog", "--history"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_list.show_history") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_history_stats():
    """--history-stats 分发到 show_history_stats"""
    test_args = ["prog", "--history-stats"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_list.show_history_stats") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_init():
    """--init 分发到 run_init"""
    test_args = ["prog", "--init"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_init.run_init") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_rebuild_last():
    """-r/--rebuild-last 分发到 run_rebuild_last"""
    test_args = ["prog", "-r"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_build.run_rebuild_last") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_interactive():
    """-i 分发到 run_interactive_build"""
    test_args = ["prog", "-i"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_interactive.run_interactive_build") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_default_build():
    """无特殊参数时执行构建"""
    test_args = ["prog", "-e", "dev"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_build.run_build") as mock_fn,
    ):
        main()
        mock_fn.assert_called_once()


def test_main_debug_mode():
    """-d 启用调试模式"""
    test_args = ["prog", "-d", "--list-envs"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_list.list_environments") as mock_fn,
        patch("jenkins_config.cli.set_debug_mode") as mock_debug,
    ):
        main()
        mock_debug.assert_called_once_with(True)


def test_main_keyboard_interrupt():
    """运行构建时 Ctrl+C 退出码 130"""
    test_args = ["prog", "-e", "dev"]
    with (
        patch.object(sys, "argv", test_args),
        patch("jenkins_config.cmd_build.run_build",
              side_effect=KeyboardInterrupt()),
    ):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 130
