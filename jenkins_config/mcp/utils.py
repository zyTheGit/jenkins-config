"""
MCP Tools 共享工具函数

提供配置加载、路径解析、客户端创建、错误返回等公共功能，
供各 MCP Tool / Resource 函数复用，避免重复代码。
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from jenkins_config.paths import (
    CONFIG_ENV_VAR,
    CONFIG_FILE_NAMES,
    env_config_file,
    resolve_config_file,
    search_bases,
    resolve_history_path as _resolve_history_path,
)


logger = logging.getLogger(__name__)

# 被视为"开启"的环境变量取值（统一一处，避免各处枚举漂移）
TRUTHY_VALUES = ("1", "true", "yes", "on")



# 开启写操作（触发构建、覆写配置）所需的环境变量
WRITE_ENV_VAR = "JENKINS_MCP_ALLOW_WRITE"

# 直连模式允许访问的 Jenkins 主机白名单环境变量（逗号分隔）
ALLOWED_HOSTS_ENV_VAR = "JENKINS_MCP_ALLOWED_HOSTS"

# 允许读写配置文件的额外根目录白名单环境变量（os.pathsep 分隔）
CONFIG_ROOTS_ENV_VAR = "JENKINS_MCP_CONFIG_ROOTS"

__all__ = [
    "CONFIG_ENV_VAR",
    "CONFIG_FILE_NAMES",
    "WRITE_ENV_VAR",
    "ALLOWED_HOSTS_ENV_VAR",
    "CONFIG_ROOTS_ENV_VAR",
    "TRUTHY_VALUES",
    "env_truthy",
    "allowed_config_bases",
    "resolve_config_path",
    "resolve_history_path",
    "history_manager",
    "get_config",
    "build_jenkins_client",
    "jenkins_client",
    "write_allowed",
    "write_denied_message",
    "host_allowed",
    "trusted_server_url",
    "dump_json",
    "environments_payload",
    "projects_payload",
    "failure_result",
]


def allowed_config_bases() -> list[Path]:
    """获取允许读写配置文件的根目录列表

    MCP 客户端可以任意指定 config_path，若不加约束，既能让凭据发往
    任意 server.url（配置文件由调用方自带），也能让 save_config 覆写
    工作目录之外的任意 YAML。因此把可用目录收敛为：

    1. jenkins_config.paths.search_bases()（项目根 / CWD / exe 目录 / 用户配置目录）；
    2. 环境变量 JENKINS_MCP_CONFIG_ROOTS 显式追加的目录（os.pathsep 分隔）。

    JENKINS_MCP_CONFIG 指向的文件由 resolve_config_path 单独按**精确文件**放行，
    不在这里追加其父目录——否则部署方把配置放在宽目录（如家目录）时，
    整棵目录树都会变成可读写范围。

    自动探测出的候选目录还会过一道"过宽"过滤：stdio 拉起时 CWD 不可控，
    可能就是文件系统根或用户家目录，那样 _is_within 对任意路径都成立，
    整个白名单等于全放行。这类候选直接剔除；
    JENKINS_MCP_CONFIG_ROOTS 是部署方显式设定的，不参与过滤。

    Returns:
        已解析为绝对路径的根目录列表

    Example:
        >>> len(allowed_config_bases()) >= 2
        True
    """
    bases = [base for base in (b.resolve() for b in search_bases()) if not _is_too_broad(base)]
    for item in os.environ.get(CONFIG_ROOTS_ENV_VAR, "").split(os.pathsep):
        root = item.strip()
        if root:
            bases.append(Path(root).resolve())
    return bases


def _is_too_broad(base: Path) -> bool:
    """判断候选根目录是否过宽，不足以作为安全边界

    Args:
        base: 已解析为绝对路径的候选根目录

    Returns:
        base 是文件系统根或用户家目录本身时返回 True

    Example:
        >>> _is_too_broad(Path(Path.cwd().anchor))
        True
    """
    if base.parent == base:
        logger.warning("候选目录 %s 为文件系统根，已从配置白名单剔除", base)
        return True
    try:
        home = Path.home().resolve()
    except RuntimeError:
        return False
    if base == home:
        logger.warning("候选目录 %s 为用户家目录，已从配置白名单剔除", base)
        return True
    return False


def resolve_config_path(config_path: str = "") -> str:
    """解析配置文件路径，为空则按环境变量 / 自动探测

    优先级：显式参数 > JENKINS_MCP_CONFIG（仅 MCP 侧生效，绝对路径）> 候选目录探测。
    环境变量在这里折算为 resolve_config_file 的入参，paths 模块本身不读它，
    避免该变量顺带改变 CLI 的自动探测结果。

    解析结果必须落在 allowed_config_bases() 之内，或恰好等于 JENKINS_MCP_CONFIG
    指向的那一个文件（部署方设定，属可信来源），否则调用方可以用自带的配置文件
    绕过主机白名单或覆写任意文件。

    Args:
        config_path: 显式指定的配置文件路径，为空时按环境变量 / 自动检测

    Returns:
        配置文件的绝对路径字符串

    Raises:
        PermissionError: 解析结果不在允许范围内

    Example:
        >>> resolve_config_path()  # doctest: +SKIP
        '/path/to/jenkins-config.yaml'
    """
    from_env = env_config_file()
    if not config_path and from_env is not None:
        config_path = str(from_env)

    resolved = resolve_config_file(config_path).resolve()
    if from_env is not None and resolved == from_env.resolve():
        return str(resolved)

    bases = allowed_config_bases()
    if not any(_is_within(resolved, base) for base in bases):
        raise PermissionError(
            f"配置文件路径不在允许范围内: {resolved}（"
            f"请通过环境变量 {CONFIG_ROOTS_ENV_VAR} 追加允许的根目录）"
        )
    return str(resolved)


def _is_within(path: Path, base: Path) -> bool:
    """判断路径是否位于指定根目录之内

    Args:
        path: 已解析为绝对路径的目标路径
        base: 已解析为绝对路径的根目录

    Returns:
        path 等于 base 或位于 base 之下时返回 True

    Example:
        >>> _is_within(Path.cwd() / "sub" / "c.yaml", Path.cwd())
        True
    """
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def resolve_history_path(config_path: str = "") -> Path:
    """解析构建历史文件路径

    默认锚定到配置文件所在目录的 data/build_history.json；
    配置来自用户级配置目录时改锚到用户级数据目录（npx / EXE 部署场景），
    实现委托给 jenkins_config.paths.resolve_history_path。

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        历史文件的 Path 对象

    Example:
        >>> resolve_history_path()  # doctest: +SKIP
        PosixPath('/path/to/data/build_history.json')
    """
    return _resolve_history_path(resolve_config_path(config_path))


def history_manager(config_path: str = "", history_file: str = "") -> Any:
    """创建只读的 HistoryManager（MCP 侧唯一入口）

    MCP 的查询与重建都不应因为读取而创建历史文件，
    因此统一传 create=False，把"只读无副作用"的约定收敛到一处。

    Args:
        config_path: 配置文件路径，为空时自动检测
        history_file: 显式指定的历史文件路径，优先于 config_path 推导结果

    Returns:
        HistoryManager 实例（create=False）

    Example:
        >>> history_manager("/tmp/jenkins-config.yaml")  # doctest: +SKIP
        <jenkins_config.history.HistoryManager object at ...>
    """
    from jenkins_config.history import HistoryManager

    path = history_file or str(resolve_history_path(config_path))
    return HistoryManager(path, create=False)


def get_config(config_path: str = "") -> Any:
    """获取 Config 实例

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        Config 实例，包含 server、build、environments 等配置

    Example:
        >>> config = get_config()
        >>> config.server.url  # doctest: +SKIP
        'http://jenkins.example.com'
    """
    from jenkins_config.config import Config

    return Config.load(resolve_config_path(config_path))


def build_jenkins_client(config: Any) -> Any:
    """根据 Config 创建 JenkinsClient

    超时统一取 config.build.curl_timeout，避免各调用点退回客户端默认值。

    Args:
        config: Config 实例

    Returns:
        JenkinsClient 实例

    Example:
        >>> client = build_jenkins_client(get_config())  # doctest: +SKIP
    """
    from jenkins_config.jenkins import JenkinsClient

    return JenkinsClient(
        url=config.server.url,
        token=config.server.token,
        username=config.server.username,
        timeout=config.build.curl_timeout,
    )


@contextmanager
def jenkins_client(config_path: str = "") -> Iterator[Any]:
    """以上下文管理方式获取 JenkinsClient，退出时关闭底层 Session

    MCP Server 是长驻进程，每次调用都新建客户端时必须显式关闭，
    否则连接池释放只能依赖 GC。

    Args:
        config_path: 配置文件路径，为空时自动检测

    Yields:
        JenkinsClient 实例

    Example:
        >>> with jenkins_client() as client:  # doctest: +SKIP
        ...     client.health_check()
    """
    client = build_jenkins_client(get_config(config_path))
    try:
        yield client
    finally:
        client.close()


def env_truthy(name: str, extra: tuple[str, ...] = ()) -> bool:
    """判断环境变量取值是否表示"开启"

    Args:
        name: 环境变量名
        extra: 除 TRUTHY_VALUES 之外额外认可的取值（需为小写）

    Returns:
        取值命中真值枚举时返回 True

    Example:
        >>> import os
        >>> os.environ["JENKINS_MCP_DEMO_FLAG"] = "ON"
        >>> env_truthy("JENKINS_MCP_DEMO_FLAG")
        True
        >>> del os.environ["JENKINS_MCP_DEMO_FLAG"]
    """
    value = os.environ.get(name, "").strip().lower()
    return value in TRUTHY_VALUES + extra


def write_allowed() -> bool:
    """判断是否允许执行写操作（触发构建、覆写配置）

    默认只读。需显式设置环境变量 JENKINS_MCP_ALLOW_WRITE=1（或 true/yes/on）
    才放开写操作，避免 AI 客户端在未经确认的情况下触发生产构建或改写配置。

    Returns:
        允许写操作时返回 True

    Example:
        >>> import os
        >>> os.environ["JENKINS_MCP_ALLOW_WRITE"] = "1"
        >>> write_allowed()
        True
    """
    return env_truthy(WRITE_ENV_VAR)


def write_denied_message(action: str) -> str:
    """生成写操作被拒绝时的提示文案

    Args:
        action: 被拒绝的动作描述，如 "触发构建"

    Returns:
        提示用户如何放开写权限的错误文案

    Example:
        >>> write_denied_message("触发构建")
        '已禁止写操作（触发构建）：请设置环境变量 JENKINS_MCP_ALLOW_WRITE=1 后重试'
    """
    return (
        f"已禁止写操作（{action}）："
        f"请设置环境变量 {WRITE_ENV_VAR}=1 后重试"
    )


def trusted_server_url() -> str:
    """读取可信的 Jenkins 地址（仅取自动锚定的配置文件）

    只读取 resolve_config_path("") 自动探测到的配置，
    **不接受调用方传入的 config_path**：否则调用方可以用一份自带的
    server.url 指向任意主机的配置，把白名单这道防护绕过去。

    Note:
        自动探测的候选目录包含进程 CWD（EXE 模式下还排在首位），
        而 MCP Server 的 CWD 由客户端启动参数决定。需要确定性地指定配置文件时，
        应设置 JENKINS_MCP_CONFIG；需要严格限定目标主机时，
        应显式设置 JENKINS_MCP_ALLOWED_HOSTS——该环境变量一旦非空即为
        唯一权威来源，见 host_allowed。

    Returns:
        自动锚定配置中的 server.url；读取失败时返回空字符串

    Example:
        >>> trusted_server_url()  # doctest: +SKIP
        'http://jenkins.example.com'
    """
    try:
        config = get_config("")
        return getattr(config.server, "url", "") or ""
    except Exception:
        return ""


def host_allowed(url: str) -> bool:
    """校验直连模式的 Jenkins 地址是否在允许范围内

    允许来源按优先级：
    1. 环境变量 JENKINS_MCP_ALLOWED_HOSTS（逗号分隔）非空时为**唯一**权威来源，
       此时不再叠加配置文件里的主机——否则客户端只要在自己的 CWD 放一份
       jenkins-config.yaml，就能把任意主机加进白名单；
    2. 该环境变量未设置时，退回自动锚定配置中的 server.url（见 trusted_server_url）。

    两者都取不到时一律拒绝（fail-closed），避免凭据被发往任意地址。

    Args:
        url: 待校验的 Jenkins 地址

    Returns:
        地址被允许时返回 True

    Example:
        >>> import os
        >>> os.environ["JENKINS_MCP_ALLOWED_HOSTS"] = "jenkins.example.com"
        >>> host_allowed("http://jenkins.example.com/")
        True
        >>> host_allowed("http://evil.example.com")
        False
    """
    from urllib.parse import urlparse

    target = (urlparse(url).hostname or "").lower()
    if not target:
        return False

    allowed = set()
    for item in os.environ.get(ALLOWED_HOSTS_ENV_VAR, "").split(","):
        host = item.strip().lower()
        if host:
            allowed.add(host)

    # 环境变量已显式声明白名单时不再叠加配置文件来源
    if not allowed:
        config_host = (urlparse(trusted_server_url()).hostname or "").lower()
        if config_host:
            allowed.add(config_host)

    return target in allowed


def dump_json(payload: Any) -> str:
    """按统一参数序列化为 JSON 字符串

    Args:
        payload: 可序列化对象

    Returns:
        缩进 2 空格、保留非 ASCII 字符的 JSON 字符串

    Example:
        >>> dump_json({"name": "开发"})
        '{\\n  "name": "开发"\\n}'
    """
    return json.dumps(payload, ensure_ascii=False, indent=2)


def environments_payload(config: Any) -> list[dict[str, str]]:
    """组装环境列表数据（tool 与 resource 共用）

    Args:
        config: Config 实例

    Returns:
        环境信息列表，每项包含 name 和 description

    Example:
        >>> environments_payload(get_config())  # doctest: +SKIP
        [{'name': 'dev', 'description': '开发环境'}]
    """
    return [
        {"name": name, "description": desc}
        for name, desc in config.list_environments()
    ]


def projects_payload(config: Any, env: str | None = None) -> list[dict[str, str]]:
    """组装项目列表数据（tool 与 resource 共用）

    Args:
        config: Config 实例
        env: 按环境名称过滤，为 None 时列出所有环境的项目

    Returns:
        项目列表，每项包含 environment、name 和 path

    Example:
        >>> projects_payload(get_config(), "dev")  # doctest: +SKIP
        [{'environment': 'dev', 'name': 'project-a', 'path': 'project-a'}]
    """
    return [
        {"environment": e, "name": name, "path": path}
        for e, name, path in config.list_projects(env)
    ]


def failure_result(message: str, job_key: str = "") -> dict[str, Any]:
    """构造 trigger_build / rebuild_last 的失败返回体

    Args:
        message: 错误信息
        job_key: 相关的 Job 标识，未知时留空

    Returns:
        包含空 triggered 列表和单条 failed 记录的字典

    Example:
        >>> failure_result("配置文件不存在")
        {'triggered': [], 'failed': [{'job_key': '', 'error': '配置文件不存在'}]}
    """
    return {"triggered": [], "failed": [{"job_key": job_key, "error": message}]}
