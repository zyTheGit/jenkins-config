# jenkins_config/cmd_list.py
"""
列表和历史命令模块
"""

from pathlib import Path

from .config import Config
from .history import HistoryManager
from .utils import print_header, format_duration


def list_environments(config_file: Path):
    """列出所有环境"""
    config = Config.load(str(config_file))
    print_header("所有环境")
    for env, desc in config.list_environments():
        if desc:
            print(f"  - {env} ({desc})")
        else:
            print(f"  - {env}")


def list_projects(config_file: Path, env: str | None):
    """列出项目"""
    config = Config.load(str(config_file))

    if env:
        print_header(f"环境 '{env}' 的项目")
        for e, name, path in config.list_projects(env):
            print(f"  - {name} ({path})")
    else:
        print_header("所有环境的项目")
        current_env = None
        for e, name, path in config.list_projects():
            if e != current_env:
                desc = config.environments[e].description if e in config.environments else ""
                header = f"\n[{e}]" if not desc else f"\n[{e}] ({desc})"
                print(header)
                current_env = e
            print(f"  - {name} ({path})")


def show_history(config_file: Path, env: str | None):
    """显示构建历史"""
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    records = manager.list(env=env, limit=20)

    print_header("构建历史")
    if not records:
        print("暂无记录")
        return

    for r in records:
        status_icon = "[OK]" if r.status == "SUCCESS" else "[FAIL]"
        print(
            f"  {status_icon} [{r.timestamp}] {r.job_key} #{r.build_num}"
            f" - {r.status} ({format_duration(r.duration)})"
        )


def show_history_stats(config_file: Path):
    """显示历史统计"""
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    stats = manager.stats()

    print_header("历史统计")
    print(f"  总构建数: {stats['total']}")
    print(f"  成功数: {stats['success']}")
    print(f"  失败数: {stats['failure']}")
    print(f"  成功率: {stats['success_rate']}%")
