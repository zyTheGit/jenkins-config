"""
路径解析模块 - 配置文件与数据文件的统一锚定规则

CLI（cli.py / cmd_build.py / cmd_list.py）与 MCP Server（mcp/utils.py）
都必须通过本模块解析路径，避免两侧各自实现导致规则漂移。

锚定规则：
1. 显式绝对路径原样使用；
2. 显式相对路径按运行模式在候选目录中查找，找不到则回退第一个候选目录；
3. 未指定路径时在候选目录中按 CONFIG_FILE_NAMES 顺序探测；
4. 候选目录顺序：
   - 源码模式：项目根目录 → 进程当前工作目录
   - EXE 冻结模式：进程当前工作目录 → exe 所在目录
"""

from __future__ import annotations

import sys
from pathlib import Path

# 配置文件自动探测的候选文件名（按优先级）
CONFIG_FILE_NAMES = (
    "jenkins-config.yaml",
    "jenkins-config.yml",
    "jenkins-config.json",
)

# 数据目录与历史文件名（相对配置文件所在目录）
DATA_DIR_NAME = "data"
HISTORY_FILE_NAME = "build_history.json"


def project_root() -> Path:
    """
    获取源码模式下的项目根目录

    Returns:
        项目根目录路径（本文件位于 jenkins_config/ 下，故上溯一级）

    Example:
        >>> project_root().name  # doctest: +SKIP
        'jenkins-config'
    """
    return Path(__file__).resolve().parent.parent


def search_bases() -> list[Path]:
    """
    获取配置文件探测的候选目录（按优先级排列）

    Returns:
        候选目录列表：源码模式为 [项目根, CWD]，EXE 模式为 [CWD, exe 目录]

    Example:
        >>> len(search_bases())
        2
    """
    if getattr(sys, "frozen", False):
        return [Path.cwd(), Path(sys.executable).resolve().parent]
    return [project_root(), Path.cwd()]


def resolve_relative(config_file: Path) -> Path:
    """
    将相对路径按运行模式锚定到具体目录

    Args:
        config_file: 相对路径

    Returns:
        第一个存在该文件的候选目录下的路径；都不存在时回退第一个候选目录

    Example:
        >>> resolve_relative(Path("jenkins-config.yaml")).is_absolute()
        True
    """
    bases = search_bases()
    for base in bases:
        candidate = base / config_file
        if candidate.exists():
            return candidate
    return bases[0] / config_file


def resolve_config_file(config_arg: str | Path = "") -> Path:
    """
    解析配置文件路径

    Args:
        config_arg: 用户指定的路径，为空时自动探测

    Returns:
        配置文件路径；均未找到时返回首个候选目录下的默认 yaml 路径（便于报错提示）

    Example:
        >>> resolve_config_file("/tmp/my.yaml").as_posix()
        '/tmp/my.yaml'
    """
    if config_arg:
        path = Path(config_arg)
        if path.is_absolute():
            return path
        return resolve_relative(path)

    bases = search_bases()
    for base in bases:
        for name in CONFIG_FILE_NAMES:
            candidate = base / name
            if candidate.exists():
                return candidate

    return bases[0] / CONFIG_FILE_NAMES[0]


def resolve_history_path(config_file: str | Path = "") -> Path:
    """
    解析构建历史文件路径

    锚定到配置文件所在目录的 data/build_history.json，
    这是历史文件路径的唯一入口，CLI 与 MCP 都应通过它取值。

    Args:
        config_file: 配置文件路径，为空时先自动探测配置文件

    Returns:
        历史文件的 Path 对象

    Example:
        >>> resolve_history_path("/tmp/jenkins-config.yaml").as_posix()
        '/tmp/data/build_history.json'
    """
    base = Path(config_file) if config_file else resolve_config_file()
    return base.parent / DATA_DIR_NAME / HISTORY_FILE_NAME
