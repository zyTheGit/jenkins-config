# jenkins_config/cmd_interactive.py
"""
交互式构建命令模块

提供交互式界面让用户选择环境、项目和构建模式。
在任意步骤按 ESC 可退回到上一步重新选择。
"""

import sys
from pathlib import Path

import questionary
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import LayoutDimension

from .config import Config
from .utils import CUSTOM_STYLE, log_error, log_info, log_warn, print_header, print_sep

# ESC 返回哨兵：questionary 的 ESC 键绑定用它作为退出结果
_BACK = object()

# 底部快捷键提示栏（跟随每一步骤动态更新）
_HINT = "↑/↓ 移动 · 空格 选择 · 回车 确认 · ESC 返回上一步 · Ctrl+C 退出"
_hint_tokens = None


def _set_hint(text: str) -> None:
    """更新下一步骤的底部快捷键提示"""
    global _hint_tokens
    _hint_tokens = HTML(f"<ansigray>{text}</ansigray>")


def _install_esc_back(question):
    """
    为 question 的 Application 追加 ESC 键绑定与底部快捷键提示栏

    兼容测试中对 questionary 工厂函数的 mock（mock 对象上设置属性为无操作的 MagicMock）。
    """
    kb = KeyBindings()

    @kb.add(Keys.Escape, eager=True)
    def _esc(event):
        event.app.exit(result=_BACK)

    question.application.key_bindings.bindings.extend(kb.bindings)

    # 在布局底部注入提示栏窗口（提示内容来自全局 _hint_tokens）
    try:
        hint_window = Window(
            height=LayoutDimension.exact(1),
            content=FormattedTextControl(lambda: _hint_tokens),
            always_hide_cursor=True,
        )
        question.application.layout.container = HSplit(
            [question.application.layout.container, hint_window]
        )
    except ValueError:
        # 测试 mock 的 layout.container 不是真实容器，跳过提示栏注入
        pass

    return question


def _ask(question, hint=_HINT):
    """安装 ESC 键绑定与底部提示栏后提问，返回用户输入（ESC 返回 _BACK，Ctrl+C/EOF 返回 None）"""
    _set_hint(hint)
    return _install_esc_back(question).ask()


def _ask_build_method():
    """步骤 1：选择构建方式"""
    return _ask(
        questionary.select(
            "请选择构建方式:",
            choices=[
                questionary.Choice(
                    title="按环境构建 - 选择一个环境，然后选择项目",
                    value="by_env",
                ),
                questionary.Choice(
                    title="按项目构建 - 从所有项目中多选", value="by_project"
                ),
            ],
            style=CUSTOM_STYLE,
        )
    )


def _ask_env(config):
    """步骤 2：选择环境"""
    env_choices = [
        questionary.Choice(
            title=f"{env} ({desc})" if desc else env,
            value=env,
        )
        for env, desc in config.list_environments()
    ]
    return _ask(
        questionary.select(
            "请选择要构建的环境:", choices=env_choices, style=CUSTOM_STYLE
        )
    )


def _ask_projects_env(config, env):
    """步骤 3：按环境选择项目"""
    projects = config.list_projects(env)
    if not projects:
        return None

    project_choices = [
        questionary.Choice(title=f"{name} ({path})", value=f"{env}:{name}")
        for _, name, path in projects
    ]

    all_choice = questionary.Choice(
        title="【全选】构建该环境所有项目", value="__ALL__"
    )
    project_choices.insert(0, all_choice)

    return questionary.checkbox(
        "请选择要构建的项目:",
        choices=project_choices,
        style=CUSTOM_STYLE,
    )


def _ask_projects_all(config):
    """步骤 3：从所有环境中多选项目"""
    all_projects = config.list_projects()
    if not all_projects:
        return None

    project_choices = []
    current_env = None

    for env, name, path in all_projects:
        if env != current_env:
            project_choices.append(
                questionary.Choice(
                    title=f"─── [{env}] ───",
                    disabled="disabled",
                    value=f"separator_{env}",
                )
            )
            current_env = env

        project_choices.append(
            questionary.Choice(title=f"  {name} ({path})", value=f"{env}:{name}")
        )

    return questionary.checkbox(
        "请选择要构建的项目:",
        choices=project_choices,
        style=CUSTOM_STYLE,
    )


def _resolve_jobs(config, build_method, selected_env, jobs_filter):
    """根据当前选择解析待构建的 Job 列表"""
    if build_method == "by_env" and jobs_filter is None:
        return config.get_jobs(env=selected_env)
    elif build_method == "by_env":
        return config.get_jobs(env=selected_env, jobs=jobs_filter)
    else:
        return config.get_jobs(jobs=jobs_filter)


def _ask_build_mode():
    """步骤 4：选择构建模式"""
    return _ask(
        questionary.select(
            "请选择构建模式:",
            choices=[
                questionary.Choice(
                    title="并行构建 (同时构建所有项目)", value="parallel"
                ),
                questionary.Choice(
                    title="顺序构建 (按顺序逐个构建)", value="sequential"
                ),
            ],
            style=CUSTOM_STYLE,
        )
    )


def _ask_confirm(jobs):
    """步骤 5：展示构建清单并确认"""
    print()
    print_sep("-")
    print(f"即将构建以下 {len(jobs)} 个项目:")
    print_sep("-")
    for job in jobs:
        print(f"  - [{job.env}] {job.key} ({job.path}) - 分支: {job.branch}")
    print_sep("-")

    return _ask(
        questionary.confirm("确认开始构建?", default=True, style=CUSTOM_STYLE),
        hint="y/n 确认 · ESC 返回上一步 · Ctrl+C 退出",
    )


def run_interactive_build(config_file: Path, args):
    """
    交互式构建选择

    让用户通过界面选择构建方式、环境/项目、构建模式，确认后执行构建。
    各步骤按 ESC 可退回到上一步重新选择，Ctrl+C/Q 或 EOF 取消退出。
    """
    print_header("交互式构建选择")

    try:
        config = Config.load(str(config_file))
    except FileNotFoundError as e:
        log_error(str(e))
        sys.exit(1)

    log_info("正在初始化交互界面...")

    # 已确认的步骤状态（回退时按需重置）
    build_method = None
    selected_env = None
    jobs_filter = None
    build_mode = None
    jobs = None

    # 状态机：1 方式 → 2 环境 → 3 项目 → 4 模式 → 5 确认
    step = 1

    while True:
        # ====================================================================
        # 步骤 1：选择构建方式（ESC 重新选择）
        # ====================================================================
        if step == 1:
            answer = _ask_build_method()
            if answer is None:
                log_warn("已取消")
                sys.exit(0)
            if answer is _BACK:
                continue  # 第一步没有上一步，重新选择
            build_method = answer
            selected_env = None
            jobs_filter = None
            step = 2

        # ====================================================================
        # 步骤 2：按环境构建时选择环境
        # ====================================================================
        elif step == 2:
            if build_method != "by_env":
                step = 3
                continue

            if not config.list_environments():
                log_error("没有可用的环境")
                sys.exit(1)

            answer = _ask_env(config)
            if answer is None:
                log_warn("已取消")
                sys.exit(0)
            if answer is _BACK:
                step = 1
                continue
            selected_env = answer
            jobs_filter = None
            step = 3

        # ====================================================================
        # 步骤 3：选择要构建的项目
        # ====================================================================
        elif step == 3:
            question = (
                _ask_projects_env(config, selected_env)
                if build_method == "by_env"
                else _ask_projects_all(config)
            )
            if question is None:
                if build_method == "by_env":
                    log_error(f"环境 '{selected_env}' 没有可用的项目")
                else:
                    log_error("没有可用的项目")
                sys.exit(1)

            selected_projects = _ask(question)

            if selected_projects is _BACK:
                step = 2 if build_method == "by_env" else 1
                continue
            if selected_projects is None:
                log_warn("已取消")
                sys.exit(0)
            if not selected_projects:
                log_warn("请至少选择一个项目（使用空格选择，回车确认）")
                sys.exit(0)

            jobs_filter = (
                None if "__ALL__" in selected_projects else selected_projects
            )
            step = 4

        # ====================================================================
        # 步骤 4：选择构建模式
        # ====================================================================
        elif step == 4:
            jobs = _resolve_jobs(config, build_method, selected_env, jobs_filter)
            if not jobs:
                log_error("没有找到匹配的项目")
                sys.exit(1)

            if len(jobs) == 1:
                build_mode = "parallel"
                log_info("仅一个构建工程，自动使用并行模式")
                step = 5
                continue

            answer = _ask_build_mode()
            if answer is None:
                log_warn("已取消")
                sys.exit(0)
            if answer is _BACK:
                step = 3
                continue
            build_mode = answer
            step = 5

        # ====================================================================
        # 步骤 5：确认构建
        # ====================================================================
        elif step == 5:
            answer = _ask_confirm(jobs)
            if answer is None or answer is False:
                log_warn("已取消")
                sys.exit(0)
            if answer is _BACK:
                # 单项目时模式被自动跳过，回退到构建模式的上一步存在歧义；
                # 直接回到项目选择，让用户重新调整所选项目
                step = 3
                continue
            break  # 确认构建

        else:  # pragma: no cover - 防御性分支
            step = 1

    # ========================================================================
    # 步骤 6：执行构建
    # ========================================================================
    print()
    args.env = selected_env
    args.mode = build_mode
    args.jobs = ",".join(jobs_filter) if jobs_filter else None
    args.branch = None
    args.yes = True

    from .cmd_build import run_build
    run_build(config_file, args)
