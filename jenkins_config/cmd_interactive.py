# jenkins_config/cmd_interactive.py
"""
交互式构建命令模块

提供交互式界面让用户选择环境、项目和构建模式。
"""

import sys
from pathlib import Path

import questionary
from questionary import Style

from .config import Config
from .utils import log_info, log_error, log_warn, print_header, print_sep

CUSTOM_STYLE = Style(
    [
        ("qmark", "fg:cyan bold"),
        ("question", "fg:white bold"),
        ("answer", "fg:green bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold"),
        ("selected", "fg:green"),
        ("separator", "fg:gray"),
        ("instruction", "fg:gray"),
        ("text", "fg:white"),
    ]
)


def run_interactive_build(config_file: Path, args):
    """
    交互式构建选择

    让用户通过界面选择：
    1. 构建方式（按环境或按项目）
    2. 要构建的环境/项目
    3. 构建模式（并行/顺序）
    4. 确认后执行构建
    """
    print_header("交互式构建选择")

    try:
        config = Config.load(str(config_file))
    except FileNotFoundError as e:
        log_error(str(e))
        sys.exit(1)

    log_info("正在初始化交互界面...")

    # ========================================================================
    # 步骤 1：选择构建方式
    # ========================================================================
    build_method = questionary.select(
        "请选择构建方式:",
        choices=[
            questionary.Choice(
                title="按环境构建 - 选择一个环境，然后选择项目", value="by_env"
            ),
            questionary.Choice(
                title="按项目构建 - 从所有项目中多选", value="by_project"
            ),
        ],
        style=CUSTOM_STYLE,
    ).ask()

    if not build_method:
        log_warn("已取消")
        sys.exit(0)

    selected_env = None
    jobs_filter = None

    # ========================================================================
    # 步骤 2：根据构建方式进行选择
    # ========================================================================
    if build_method == "by_env":
        envs = config.list_environments()
        if not envs:
            log_error("没有可用的环境")
            sys.exit(1)

        env_choices = [
            questionary.Choice(
                title=f"{env} ({desc})" if desc else env,
                value=env,
            )
            for env, desc in config.list_environments()
        ]
        selected_env = questionary.select(
            "请选择要构建的环境:", choices=env_choices, style=CUSTOM_STYLE
        ).ask()

        if not selected_env:
            log_warn("已取消")
            sys.exit(0)

        projects = config.list_projects(selected_env)
        if not projects:
            log_error(f"环境 '{selected_env}' 没有可用的项目")
            sys.exit(1)

        project_choices = [
            questionary.Choice(
                title=f"{name} ({path})", value=f"{selected_env}:{name}"
            )
            for _, name, path in projects
        ]

        all_choice = questionary.Choice(
            title="【全选】构建该环境所有项目", value="__ALL__"
        )
        project_choices.insert(0, all_choice)

        selected_projects = questionary.checkbox(
            "请选择要构建的项目 (空格选择，回车确认):",
            choices=project_choices,
            style=CUSTOM_STYLE,
        ).ask()

        if selected_projects is None:
            log_warn("已取消")
            sys.exit(0)

        if not selected_projects:
            log_warn("请至少选择一个项目（使用空格选择，回车确认）")
            sys.exit(0)

        if "__ALL__" in selected_projects:
            jobs_filter = None
        else:
            jobs_filter = selected_projects

    else:
        all_projects = config.list_projects()
        if not all_projects:
            log_error("没有可用的项目")
            sys.exit(1)

        project_choices = []
        current_env = None

        for env, name, path in all_projects:
            if env != current_env:
                project_choices.append(
                    questionary.Choice(
                        title=f"─── [{env}] ───",
                        disabled=True,
                        value=f"separator_{env}",
                    )
                )
                current_env = env

            project_choices.append(
                questionary.Choice(
                    title=f"  {name} ({path})", value=f"{env}:{name}"
                )
            )

        selected_projects = questionary.checkbox(
            "请选择要构建的项目 (空格选择，回车确认):",
            choices=project_choices,
            style=CUSTOM_STYLE,
        ).ask()

        if selected_projects is None:
            log_warn("已取消")
            sys.exit(0)

        if not selected_projects:
            log_warn("请至少选择一个项目（使用空格选择，回车确认）")
            sys.exit(0)

        jobs_filter = selected_projects

    # ========================================================================
    # 步骤 3：选择构建模式
    # ========================================================================
    if build_method == "by_env" and jobs_filter is None:
        jobs = config.get_jobs(env=selected_env)
    elif build_method == "by_env":
        jobs = config.get_jobs(env=selected_env, jobs=jobs_filter)
    else:
        jobs = config.get_jobs(jobs=jobs_filter)

    if not jobs:
        log_error("没有找到匹配的项目")
        sys.exit(1)

    if len(jobs) == 1:
        build_mode = "parallel"
        log_info("仅一个构建工程，自动使用并行模式")
    else:
        build_mode = questionary.select(
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
        ).ask()

        if not build_mode:
            log_warn("已取消")
            sys.exit(0)

    # ========================================================================
    # 步骤 4：确认构建
    # ========================================================================
    print()
    print_sep("-")
    print(f"即将构建以下 {len(jobs)} 个项目:")
    print_sep("-")
    for job in jobs:
        print(f"  - [{job.env}] {job.key} ({job.path}) - 分支: {job.branch}")
    print_sep("-")

    confirm = questionary.confirm(
        "确认开始构建?", default=True, style=CUSTOM_STYLE
    ).ask()

    if not confirm:
        log_warn("已取消")
        sys.exit(0)

    # ========================================================================
    # 步骤 5：执行构建
    # ========================================================================
    print()
    args.env = selected_env
    args.mode = build_mode
    args.jobs = ",".join(jobs_filter) if jobs_filter else None
    args.branch = None
    args.yes = True

    from .cmd_build import run_build
    run_build(config_file, args)
