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

import jenkins_config.cmd_interactive as ci
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


def _to_back(value):
    return ci._BACK if value == "__ESC__" else value


def _run_with_questionary(config_path, args, prompts):
    """
    使用 mock questionary 运行 run_interactive_build。

    prompts = {"select": [...], "checkbox": [...], "confirm": True}
    - select: 列表值表示多次 ask() 的返回序列（每个元素是标量）
    - checkbox/confirm: 元素为列表时表示多次 ask() 的返回序列
      （每个元素是一次 ask 的返回值）；标量时为单次 ask 的返回值
    "__ESC__" 标记转换为 ci._BACK 模拟 ESC。
    """
    patches = []
    for func_name, value in prompts.items():
        mock_obj = MagicMock()
        if func_name == "select" and isinstance(value, list):
            # select: 标量序列
            mock_obj.ask.side_effect = [_to_back(v) for v in value]
        elif (
            isinstance(value, list)
            and value
            and all(isinstance(v, list) for v in value)
        ):
            # checkbox/confirm: ask 序列，每个元素是一次 ask 的返回值
            mock_obj.ask.side_effect = [
                [_to_back(x) for x in v] if isinstance(v, list) else _to_back(v)
                for v in value
            ]
        else:
            mock_obj.ask.return_value = _to_back(value)
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


def test_interactive_esc_at_method_select(tmp_path):
    """第一步按 ESC -> 重新提问（仍在第一步）"""
    config_path = _setup_config(tmp_path)
    # select 第一次 ESC，第二次仍取消 -> exit(0)
    with pytest.raises(SystemExit) as exc:
        _run_with_questionary(config_path, MagicMock(), {
            "select": ["__ESC__", None],
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


def test_interactive_single_job_auto_parallel(tmp_path):
    """仅一个 Job 时自动并行，跳过模式选择"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "dev"],  # 只调用两次 select
            "checkbox": ["__ALL__"],
            "confirm": True,
        })

    assert args.mode == "parallel"
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
# ESC 快捷键（回退到上一步）
# ============================================================================


def test_interactive_esc_back_to_method_select(tmp_path):
    """按环境选择项目时 ESC -> 退回环境选择"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "dev", "dev"],  # 第三次是回退后重新选环境
            "checkbox": ["__ESC__", "__ALL__"],
            "confirm": True,
        })

    assert args.yes is True
    mock_build.assert_called_once()


def test_interactive_esc_back_from_env_to_method(tmp_path):
    """选择环境时 ESC -> 退回构建方式选择"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "__ESC__", "by_project"],
            "checkbox": ["dev:app"],
            "confirm": True,
        })

    assert args.yes is True
    mock_build.assert_called_once()


def test_interactive_esc_back_from_project_eco_to_method(tmp_path):
    """按项目选择项目时 ESC -> 退回构建方式选择"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_project", "by_project"],
            "checkbox": ["__ESC__", "dev:app"],
            "confirm": True,
        })

    assert args.yes is True
    mock_build.assert_called_once()


def test_interactive_esc_back_at_build_mode(tmp_path):
    """构建模式选择时 ESC -> 退回项目选择"""
    config_path = _setup_config_two_projects(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "dev", "__ESC__", "sequential"],
            "checkbox": ["__ALL__", "__ALL__"],  # 回退后重新选择
            "confirm": True,
        })

    assert args.mode == "sequential"
    mock_build.assert_called_once()


def test_interactive_esc_back_at_confirm(tmp_path):
    """确认时 ESC -> 退回项目选择，随后确认构建"""
    config_path = _setup_config(tmp_path)
    args = MagicMock()

    with patch("jenkins_config.cmd_build.run_build") as mock_build:
        _run_with_questionary(config_path, args, {
            "select": ["by_env", "dev"],
            "checkbox": ["__ALL__"],
            "confirm": ["__ESC__", True],
        })

    assert args.yes is True
    mock_build.assert_called_once()


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


# ============================================================================
# ESC 键绑定安装
# ============================================================================


def test_installed_esc_back_adds_escape_binding():
    """_install_esc_back 应为 Application 追加 ESC 键绑定"""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    mock_q = MagicMock()
    kb = KeyBindings()
    mock_q.application.key_bindings = kb

    before = len(kb.bindings)
    ci._install_esc_back(mock_q)

    assert len(kb.bindings) == before + 1
    assert kb.bindings[-1].keys == (Keys.Escape,)


def test_install_esc_back_handles_mock_question():
    """mock 的 question 对象（属性自动创建）不应报错"""
    ci._install_esc_back(MagicMock())
