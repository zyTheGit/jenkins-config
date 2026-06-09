# jenkins_config/cli.py
"""
命令行入口模块

这是整个工具的入口点，负责解析命令行参数并分发到各命令模块。
"""

import sys
from pathlib import Path

from .config import Config
from .utils import log_info, log_warn, set_debug_mode, log_debug


def main():
    """
    CLI 主入口函数

    解析命令行参数，根据参数调用相应的功能函数。
    在 pyproject.toml 中注册为：
        [project.scripts]
        jenkins-build = "jenkins_config.cli:main"
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Jenkins 自动构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 构建参数
    parser.add_argument(
        "-m", "--mode",
        choices=["parallel", "sequential"],
        default="parallel",
        help="构建模式：parallel（并行）或 sequential（顺序）",
    )
    parser.add_argument("-e", "--env", help="构建指定环境的所有项目")
    parser.add_argument(
        "-j", "--jobs",
        help="构建指定项目，格式: env:project,env:project",
    )
    parser.add_argument(
        "-p", "--params",
        help="额外构建参数，格式: key=value&key2=value2",
    )
    parser.add_argument(
        "-b", "--branch",
        help="自定义构建分支，覆盖配置文件中的默认分支",
    )
    parser.add_argument(
        "-c", "--config",
        default="jenkins-config.yaml",
        help="配置文件路径（默认: jenkins-config.yaml，也支持 .json）",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="交互式选择要构建的项目",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="跳过确认直接执行构建",
    )

    # 列表命令
    parser.add_argument("--list-envs", action="store_true", help="列出所有环境")
    parser.add_argument(
        "--list-projects",
        metavar="ENV",
        nargs="?",
        const="",
        help="列出项目，不指定 ENV 则列出所有",
    )

    # 历史命令
    parser.add_argument("--history", action="store_true", help="查看构建历史")
    parser.add_argument("--history-stats", action="store_true", help="查看历史统计")
    parser.add_argument(
        "-r", "--rebuild-last", action="store_true", help="重建上次构建的项目"
    )

    # 其他选项
    parser.add_argument("-d", "--debug", action="store_true", help="调试模式")
    parser.add_argument(
        "--help-config",
        action="store_true",
        help="显示配置文件模板（含必填/可选字段说明）",
    )

    # 初始化命令
    parser.add_argument(
        "--init", action="store_true",
        help="生成配置文件模板（结合 -i 交互式引导）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制覆盖已存在的配置文件（结合 --init 使用）",
    )

    args = parser.parse_args()

    # 调试模式
    if args.debug:
        set_debug_mode(True)
        log_debug("调试模式已启用")

    # 配置文件路径
    config_file = _resolve_config_path(args.config)

    # 分发命令
    if args.init:
        from .cmd_init import run_init
        run_init(config_file, args)
        return

    if args.list_envs:
        from .cmd_list import list_environments
        list_environments(config_file)
        return

    if args.list_projects is not None:
        from .cmd_list import list_projects
        list_projects(config_file, args.list_projects)
        return

    if args.history:
        from .cmd_list import show_history
        show_history(config_file, args.env)
        return

    if args.history_stats:
        from .cmd_list import show_history_stats
        show_history_stats(config_file)
        return

    if args.help_config:
        Config.show_template()
        return

    if args.rebuild_last:
        from .cmd_build import run_rebuild_last
        run_rebuild_last(config_file, args)
        return

    if args.interactive:
        from .cmd_interactive import run_interactive_build
        run_interactive_build(config_file, args)
        return

    # 默认：执行构建
    try:
        from .cmd_build import run_build
        run_build(config_file, args)
    except KeyboardInterrupt:
        print("\n")
        log_warn("用户取消操作")
        sys.exit(130)


def _resolve_config_path(config_arg: str) -> Path:
    """
    解析配置文件路径

    根据运行模式（源码/exe）确定配置文件的绝对路径。

    Args:
        config_arg: 用户传入的路径参数

    Returns:
        配置文件的绝对路径
    """
    config_file = Path(config_arg)

    if config_file.is_absolute():
        return config_file

    if getattr(sys, "frozen", False):
        # PyInstaller exe 模式
        exe_dir = Path(sys.executable).parent

        cwd_config = Path.cwd() / config_arg
        if cwd_config.exists():
            return cwd_config

        exe_config = exe_dir / config_arg
        if exe_config.exists():
            return exe_config

        return cwd_config
    else:
        # 源码模式：相对于项目根目录
        script_dir = Path(__file__).parent.parent
        return script_dir / config_arg


if __name__ == "__main__":
    main()
