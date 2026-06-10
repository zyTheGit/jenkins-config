# tests/test_cmd_interactive.py
r"""
交互式构建命令模块测试

使用 return_value 方式 mock questionary 工厂函数。
questionary.select("...") 返回一个对象，该对象的 .ask() 返回用户选择。
"""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jenkins_config.cmd_interactive import run_interactive_build


# ============================================================================
# 辅助函数
# ============================================================================


def _setup_config(tmp_path) -> str:
    """创建单项目测试配置"""
    config_file = tmp_path / "jenkins-config.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "token"\n'
        "environments:\n"
        "  dev:\n"
        '    description: "dev"\n'
        "    params:\n"
        '      branch: "develop"\n'
        "    projects:\n"
        "      - name: app\n",
        encoding="utf-8",
    )
    return str(config_file)


def _setup_config_two_projects(tmp_path) -> str:
    """创建双项目测试配置（用于测试构建模式选择）"""
    config_file = tmp_path / "jenkins-config2.yaml"
    config_file.write_text(
        "server:\n"
        '  url: "http://localhost:8080"\n'
        '  token: "t"\n'
        "environments:\n"
        "  dev:\n"
        "    projects:\n"
        "      - name: app-a\n"
        "      - name: app-b\n",
        encoding="utf-8",
    )
    return str(config_file)


def _run_with_questionary(config_path, args, prompts):
    """
    使用 mock questionary 运行 run_interactive_build。

    prompts = {"select": [...], "checkbox": [...], "confirm": True}
    """
    patches = []
    for func_name, value in prompts.items():
        mock_obj = MagicMock()
        if func_name == "select":
            # select 可能需要多次调用（方法选择 + 环境选择）
            if isinstance(value, list):
                mock_obj.ask.side_effect = value
            else:
                mock_obj.ask.return_value = value
        else:
            # checkbox 返回列表、confirm/text/password 返回标量
            mock_obj.ask.return_value = value
        p = patch(
            f"jenkins_config.cmd_interactive.questionary.{func_name}",
            return_value=mock_obj,
        )
        patches.append(p)

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        # questionary.Choice 也需要 mock（它被用来构建 choices 列表）
        stack.enter_context(patch("jenkins_config.cmd_interactive.questionary.Choice"))
        return run_interactive_build(config_path, args)


# ============================================================================
# run_interactive_build
# ============================================================================


def test_interactive_config_not_found(tmp_path):
    """配置文件不存在时 exit(1)"""
    with pytest.raises(SystemExit) as exc:
        run_interactive_build(tmp_path / "nonexistent.yaml", MagicMock())
    assert exc.value.code == 1


def test_interactive_by_env_select_all(tmp_path):
    """按环境构建 - 全选 -> 调用 run_build"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "dev"],
            "checkbox": ["__ALL__"],
            "confirm": True,
        })

    assert args.yes is True
    mock_build.assert_called_once()


def test_interactive_by_env_select_specific(tmp_path):
    """按环境构建 - 选具体项目"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "dev"],
            "checkbox": ["dev:app"],
            "confirm": True,
        })

    assert args.yes is True
    mock_build.assert_called_once()


def test_interactive_by_project(tmp_path):
    """按项目构建"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": "by_project",
            "checkbox": ["dev:app"],
            "confirm": True,
        })

    assert args.yes is True
    mock_build.assert_called_once()


def test_interactive_cancel_at_method_select(tmp_path):
    """选择构建方式时取消 -> exit(0)"""
    config_path = _setup_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_with_questionary(config_path, MagicMock(), {
            "select": None,
        })
    assert exc.value.code == 0


def test_interactive_cancel_at_env_select(tmp_path):
    """选择环境时取消 -> exit(0)"""
    config_path = _setup_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_with_questionary(config_path, MagicMock(), {
            "select": ["by_env", None],
        })
    assert exc.value.code == 0


def test_interactive_cancel_at_project_select(tmp_path):
    """选择项目时取消（checkbox 返回 None）-> exit(0)"""
    config_path = _setup_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_with_questionary(config_path, MagicMock(), {
            "select": ["by_env", "dev"],
            "checkbox": None,
        })
    assert exc.value.code == 0


def test_interactive_empty_selection(tmp_path):
    """选择空列表 -> exit(0)"""
    config_path = _setup_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_with_questionary(config_path, MagicMock(), {
            "select": ["by_env", "dev"],
            "checkbox": [],
        })
    assert exc.value.code == 0


def test_interactive_sequential_mode(tmp_path):
    """顺序构建模式"""
    config_path = _setup_config_two_projects(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "dev", "sequential"],
            "checkbox": ["__ALL__"],
            "confirm": True,
        })

    assert args.mode == "sequential"
    mock_build.assert_called_once()


def test_interactive_cancel_at_confirm(tmp_path):
    """确认构建时取消 -> exit(0)"""
    config_path = _setup_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_with_questionary(config_path, MagicMock(), {
            "select": ["by_env", "dev"],
            "checkbox": ["__ALL__"],
            "confirm": False,
        })
    assert exc.value.code == 0


# ============================================================================
# 边缘路径
# ============================================================================


def test_interactive_no_environments(tmp_path):
    """环境列表为空时 exit(1)"""
    config_path = str(tmp_path / "empty.yaml")
    Path(config_path).write_text(
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments: {}\n",
        encoding="utf-8",
    )

    with patch("jenkins_config.cmd_interactive.questionary.select") as mock_select:
        mock_select.ask.return_value = "by_env"
        with pytest.raises(SystemExit) as exc:
            run_interactive_build(config_path, MagicMock())
        assert exc.value.code == 1


def test_interactive_by_env_no_projects_in_env(tmp_path):
    """环境存在但无项目时 exit(1)"""
    config_path = str(tmp_path / "empty-proj.yaml")
    Path(config_path).write_text(
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments:\n  dev:\n    projects: []\n",
        encoding="utf-8",
    )

    with (
        patch("jenkins_config.cmd_interactive.questionary.select") as mock_select,
        patch("jenkins_config.cmd_interactive.questionary.Choice"),
    ):
        mock_select.ask.side_effect = ["by_env", "dev"]
        with pytest.raises(SystemExit) as exc:
            run_interactive_build(config_path, MagicMock())
        assert exc.value.code == 1


def test_interactive_by_project_empty(tmp_path):
    """按项目但无可选项目时 exit(1)"""
    config_path = str(tmp_path / "empty.yaml")
    Path(config_path).write_text(
        "server:\n  url: http://localhost:8080\n  token: t\n"
        "environments: {}\n",
        encoding="utf-8",
    )

    with patch("jenkins_config.cmd_interactive.questionary.select") as mock_select:
        mock_select.ask.return_value = "by_project"
        with pytest.raises(SystemExit) as exc:
            run_interactive_build(config_path, MagicMock())
        assert exc.value.code == 1


def test_interactive_by_project_empty_selection(tmp_path):
    """按项目 - checkbox 返回空列表"""
    config_path = _setup_config(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_with_questionary(config_path, MagicMock(), {
            "select": "by_project",
            "checkbox": [],
        })
    assert exc.value.code == 0
