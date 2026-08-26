"""
路径解析模块 - 配置文件与数据文件的统一锚定规则

CLI（cli.py / cmd_build.py / cmd_list.py）与 MCP Server（mcp/utils.py）
都必须通过本模块解析路径，避免两侧各自实现导致规则漂移。

锚定规则：
1. 显式绝对路径原样使用；
2. 显式相对路径按运行模式在候选目录中查找，找不到则回退第一个候选目录；
3. 未指定路径时在候选目录中按 CONFIG_FILE_NAMES 顺序探测；
4. 候选目录顺序：
   - 源码模式：项目根目录 → 进程当前工作目录 → 用户级配置目录
   - EXE 冻结模式：进程当前工作目录 → exe 所在目录 → 用户级配置目录

环境变量 JENKINS_MCP_CONFIG 只对 MCP Server 生效（由 mcp/utils.resolve_config_path
在调用本模块前应用），本模块的自动探测不读取它——否则用户为 MCP 客户端导出该变量后，
CLI 在项目目录里的 `jenkins-build` 也会静默改用那份配置。

用户级目录三平台统一为 `~/.jenkins-config`：这个工具的主要部署方式是 npx / 单文件
可执行程序，用户手上没有项目目录，"配置该放哪"必须一句话讲完。按平台分散到
%LOCALAPPDATA% / ~/Library/Application Support / ~/.config 更符合系统惯例，
但要用三行才说得清，而且配置目录与数据目录在 Windows、macOS 上重合、只在 Linux 上
分开，反而多出一类平台差异。这里选可发现性，代价是不再尊重 XDG_CONFIG_HOME。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 用户级目录名（挂在家目录下，带点前缀避免污染 ls ~ 的可见输出）
APP_DIR_NAME = ".jenkins-config"

# 用户级日志子目录（相对 APP_DIR_NAME）
LOG_DIR_NAME = "logs"

# 显式指定配置文件的环境变量（仅 MCP Server 侧应用，见模块 docstring）
CONFIG_ENV_VAR = "JENKINS_MCP_CONFIG"

# 配置文件自动探测的候选文件名（按优先级）
CONFIG_FILE_NAMES = (
    "jenkins-config.yaml",
    "jenkins-config.yml",
    "jenkins-config.json",
)

# 数据目录与历史文件名（相对配置文件所在目录）
DATA_DIR_NAME = "data"
HISTORY_FILE_NAME = "build_history.json"


def user_config_dir() -> Path:
    """获取用户级配置目录（三平台统一）

    Returns:
        ~/.jenkins-config

    Raises:
        RuntimeError: 无法确定家目录（HOME / USERPROFILE 均缺失，如容器或服务账号）

    Example:
        >>> user_config_dir().name
        '.jenkins-config'
    """
    return Path.home() / APP_DIR_NAME


def user_log_dir() -> Path:
    """获取用户级日志目录

    Returns:
        ~/.jenkins-config/logs

    Raises:
        RuntimeError: 无法确定家目录（同 user_config_dir）

    Example:
        >>> user_log_dir().is_absolute()
        True
    """
    return user_config_dir() / LOG_DIR_NAME


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

    末位固定为用户级配置目录 `~/.jenkins-config`：MCP Server 由客户端以 stdio 拉起，
    CWD 可能是 `/` 或用户家目录，仅靠项目根 / CWD / exe 目录探测不可靠。
    家目录不可解析时（容器 / 服务账号）记一条 warning 并跳过该候选，
    不让整条探测链因此报错。

    Returns:
        候选目录列表：源码模式为 [项目根, CWD, 用户配置目录]，
        EXE 模式为 [CWD, exe 目录, 用户配置目录]；家目录不可用时末位缺省

    Example:
        >>> len(search_bases()) >= 2
        True
    """
    if getattr(sys, "frozen", False):
        bases = [Path.cwd(), Path(sys.executable).resolve().parent]
    else:
        bases = [project_root(), Path.cwd()]
    try:
        bases.append(user_config_dir())
    except RuntimeError as exc:
        logger.warning("无法确定家目录（%s），已跳过用户级配置目录候选", exc)
    return bases


def _expand(path: Path) -> Path:
    """展开 ~ 前缀，home 不可解析时退回原值

    expanduser 在 HOME/USERPROFILE 均缺失时抛 RuntimeError（容器 / 服务账号），
    这里兜住该异常，让调用方的 Returns 契约保持成立。

    Args:
        path: 可能含 ~ 前缀的路径

    Returns:
        展开后的路径；无法确定 home 时返回未展开的原路径

    Example:
        >>> _expand(Path("jenkins-config.yaml")).name
        'jenkins-config.yaml'
    """
    try:
        return path.expanduser()
    except RuntimeError as exc:
        logger.warning("无法展开 ~ 前缀（%s）：%s", path, exc)
        return path


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


def env_config_file() -> Path | None:
    """读取环境变量 JENKINS_MCP_CONFIG 指定的配置文件路径

    MCP Server 由客户端以 stdio 子进程方式拉起，CWD 不可控，
    因此在客户端 `mcp.json` 的 `env` 里显式注入配置路径是最可靠的方式。
    正因如此**只接受绝对路径**：相对路径仍要靠 CWD 锚定，等于把该变量
    存在的理由（确定性）又丢回去，这类取值会记一条 warning 后按未设置处理。

    只由 mcp/utils.resolve_config_path 调用，不参与 CLI 的自动探测。

    Returns:
        环境变量对应的绝对路径；未设置或取值为相对路径时返回 None

    Example:
        >>> import os
        >>> os.environ["JENKINS_MCP_CONFIG"] = str(Path.cwd() / "my.yaml")
        >>> env_config_file().name
        'my.yaml'
        >>> os.environ["JENKINS_MCP_CONFIG"] = "my.yaml"
        >>> env_config_file() is None
        True
        >>> del os.environ["JENKINS_MCP_CONFIG"]
    """
    value = os.environ.get(CONFIG_ENV_VAR, "").strip()
    if not value:
        return None
    path = _expand(Path(value))
    if not path.is_absolute():
        logger.warning(
            "%s 需要绝对路径（当前为 %s），已忽略并回退自动探测", CONFIG_ENV_VAR, value
        )
        return None
    return path


def resolve_config_file(config_arg: str | Path = "") -> Path:
    """
    解析配置文件路径

    优先级：显式参数 > 候选目录自动探测。
    环境变量 JENKINS_MCP_CONFIG 由 MCP 侧在调用本函数前折算为 config_arg，
    本函数不读取它，避免该变量影响 CLI（见模块 docstring）。

    Args:
        config_arg: 用户指定的路径，为空时自动探测

    Returns:
        配置文件路径；均未找到时返回首个候选目录下的默认 yaml 路径（便于报错提示）

    Example:
        >>> target = Path.cwd() / "my.yaml"
        >>> resolve_config_file(target) == target
        True
    """
    if config_arg:
        path = _expand(Path(config_arg))
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

    统一锚定到配置文件所在目录的 data/build_history.json。用户级目录三平台
    都是 `~/.jenkins-config`，配置与数据同处一地，因此不需要再按平台分流——
    走 npx 部署时历史落在 `~/.jenkins-config/data/`，与带版本号的 npx 缓存目录
    无关，升级不会丢。这是历史文件路径的唯一入口，CLI 与 MCP 都应通过它取值。

    Args:
        config_file: 配置文件路径，为空时先自动探测配置文件

    Returns:
        历史文件的 Path 对象

    Example:
        >>> base = Path.cwd()
        >>> resolve_history_path(base / "jenkins-config.yaml") == base / "data" / "build_history.json"
        True
    """
    base = Path(config_file) if config_file else resolve_config_file()
    return base.parent / DATA_DIR_NAME / HISTORY_FILE_NAME
