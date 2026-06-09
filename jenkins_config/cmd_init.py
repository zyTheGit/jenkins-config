# jenkins_config/cmd_init.py
"""
初始化配置命令模块

提供交互式和非交互式两种方式生成配置文件。
"""

import shutil
import sys
from pathlib import Path

import yaml
import questionary
from questionary import Style

from .config import Config, BuildConfig
from .utils import log_info, log_success, log_error, log_warn, print_header, print_sep

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


def _cli_cmd() -> str:
    """返回当前运行环境下的 CLI 命令名"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return "uv run python -m jenkins_config.cli"


def run_init(config_file: Path, args):
    """
    初始化配置文件

    优先使用示例文件，结合 -i 可进入交互式引导模式。
    """
    print_header("初始化配置文件")

    if config_file.exists() and not args.force:
        log_warn(f"配置文件已存在: {config_file}")
        try:
            print("是否覆盖? [y/N]: ", end="", flush=True)
            response = input().strip().lower()
            if response not in ("y", "yes"):
                log_info("已取消")
                return
        except (KeyboardInterrupt, EOFError):
            print()
            log_info("已取消")
            return

    config_file.parent.mkdir(parents=True, exist_ok=True)

    if args.interactive:
        _run_init_interactive(config_file)
        return

    # 静默模式：优先使用示例文件（YAML 优先，JSON 后备）
    example_yaml = config_file.with_name("jenkins-config.example.yaml")
    example_json = config_file.with_name("jenkins-config.example.json")
    if example_yaml.exists():
        shutil.copy2(str(example_yaml), str(config_file))
        log_success(f"已从示例文件生成配置: {config_file}")
    elif example_json.exists():
        shutil.copy2(str(example_json), str(config_file))
        log_success(f"已从示例文件生成配置: {config_file}")
        log_info("提示: 示例文件为 JSON 格式，建议迁移到 YAML（支持注释）")
    else:
        template = Config.generate_template()
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(
                "# Jenkins 构建工具配置文件\n"
                "# 所有 Jenkins 参数放在 params 字典中\n"
                "# 新增插件参数只需在 params 中添加\n\n"
            )
            yaml.dump(
                template,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                indent=2,
            )
        log_success(f"配置文件模板已生成: {config_file}")

    log_info("请编辑配置文件修改服务器地址和 Token 后即可使用")
    log_info(f"使用 -i 参数进入交互式引导: {_cli_cmd()} --init -i")


def _run_init_interactive(config_file: Path):
    """
    交互式引导生成配置文件

    通过问答形式逐步引导用户填写服务器信息、构建配置和环境配置。
    """
    print_header("交互式初始化配置")

    # ========================================================================
    # 1. 服务器配置
    # ========================================================================
    log_info("第一步：Jenkins 服务器配置")
    print_sep("-")

    url = questionary.text(
        "Jenkins 服务器地址:",
        default="http://localhost:8080",
        qmark="*",
        style=CUSTOM_STYLE,
    ).ask()
    if not url:
        log_warn("已取消")
        return

    username = questionary.text(
        "Jenkins 用户名:", default="admin", qmark="*", style=CUSTOM_STYLE
    ).ask()
    if username is None:
        log_warn("已取消")
        return

    token = questionary.password(
        "API Token (输入不可见):", qmark="*", style=CUSTOM_STYLE
    ).ask()
    if not token:
        log_warn("已取消")
        return

    # ========================================================================
    # 2. 构建配置
    # ========================================================================
    print()
    log_info("第二步：构建行为配置")
    print_sep("-")

    use_default_build = questionary.confirm(
        "使用默认构建配置?", default=True, style=CUSTOM_STYLE
    ).ask()

    if use_default_build:
        build_conf = BuildConfig()
    else:
        mode = questionary.select(
            "构建模式:", choices=["parallel", "sequential"], style=CUSTOM_STYLE
        ).ask()
        if not mode:
            log_warn("已取消")
            return

        poll_interval = questionary.text(
            "轮询间隔（秒）:", default="10", style=CUSTOM_STYLE
        ).ask()
        build_timeout = questionary.text(
            "构建超时（秒）:", default="3600", style=CUSTOM_STYLE
        ).ask()
        log_dir = questionary.text(
            "日志目录:", default="./jenkins_logs", style=CUSTOM_STYLE
        ).ask()

        build_conf = BuildConfig(
            mode=mode,
            poll_interval=int(poll_interval) if poll_interval else 10,
            build_timeout=int(build_timeout) if build_timeout else 3600,
            log_dir=log_dir or "./jenkins_logs",
        )

    # ========================================================================
    # 3. 环境配置
    # ========================================================================
    print()
    log_info("第三步：环境配置")
    print_sep("-")

    environments = {}
    add_envs = questionary.confirm(
        "配置构建环境?", default=True, style=CUSTOM_STYLE
    ).ask()

    if add_envs:
        while True:
            print()
            print_sep("-")
            env_name = questionary.text(
                "环境名称 (如 dev, test, prod):", qmark="*", style=CUSTOM_STYLE
            ).ask()
            if not env_name:
                break

            desc = questionary.text(
                "环境描述 (如 '开发环境'):", style=CUSTOM_STYLE
            ).ask()
            default_branch = questionary.text(
                "默认 Git 分支:", default="main", style=CUSTOM_STYLE
            ).ask()

            env_config = {
                "params": {"branch": default_branch or "main"},
                "projects": [],
            }
            if desc:
                env_config["description"] = desc

            log_info(f"为环境 '{env_name}' 添加项目（留空结束）:")
            while True:
                proj_name = questionary.text(
                    "  项目名称 (Jenkins Job 名称，留空结束):", style=CUSTOM_STYLE
                ).ask()
                if not proj_name:
                    break

                proj_branch = questionary.text(
                    f"  构建分支 (留空使用环境默认 '{default_branch}'):",
                    style=CUSTOM_STYLE,
                ).ask()
                proj = {"name": proj_name}
                if proj_branch:
                    proj["params"] = {"branch": proj_branch}
                env_config["projects"].append(proj)

            if env_config["projects"]:
                environments[env_name] = env_config
                log_success(f"环境 '{env_name}' 已添加 ({len(env_config['projects'])} 个项目)")
            else:
                log_warn(f"环境 '{env_name}' 没有项目，已跳过")

            if not questionary.confirm(
                "继续添加下一个环境?", default=True, style=CUSTOM_STYLE
            ).ask():
                break

    # ========================================================================
    # 4. 组装并写入配置
    # ========================================================================
    print()
    print_sep("=")

    config_data = {
        "server": {
            "url": url,
            "username": username,
            "token": token,
        },
        "build": {
            "mode": build_conf.mode,
            "poll_interval": build_conf.poll_interval,
            "build_timeout": build_conf.build_timeout,
            "curl_timeout": build_conf.curl_timeout,
            "log_dir": build_conf.log_dir,
            "log_retention_days": build_conf.log_retention_days,
        },
    }

    if environments:
        config_data["environments"] = environments

    with open(config_file, "w", encoding="utf-8") as f:
        f.write(
            "# Jenkins 构建工具配置文件\n"
            "# 由交互式引导生成\n\n"
        )
        yaml.dump(
            config_data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            indent=2,
        )

    log_success(f"配置文件已生成: {config_file}")
    print_sep("-")
    log_info("后续操作:")
    log_info(f"  1. 验证配置: {_cli_cmd()} --list-envs")
    log_info(f"  2. 开始构建: {_cli_cmd()} -i")
