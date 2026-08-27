"""
MCP Tools 共享工具函数

提供配置加载、路径解析、客户端创建、错误返回等公共功能，
供各 MCP Tool / Resource 函数复用，避免重复代码。
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from jenkins_config.config_io import PLACEHOLDER_VALUES
from jenkins_config.mcp.errors import ErrorCode, classify, failure_payload
from jenkins_config.paths import (
    APP_DIR_NAME,
    CONFIG_ENV_VAR,
    CONFIG_FILE_NAMES,
    env_config_file,
    probe_report,
    resolve_config_file,
    search_bases,
    user_config_dir,
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
    "is_base_too_broad",
    "probe_report_for_mcp",
    "resolve_config_path",
    "backup_config_file",
    "ConfigInspection",
    "inspect_config",
    "config_failure_payload",
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


def is_base_too_broad(base: Path) -> bool:
    """判断候选根目录是否因过宽而被排除在配置白名单之外（公开包装）

    诊断类 tool（where_config）需要向外解释"这个候选为什么没进白名单"，
    但直接引用 _is_too_broad 会让诊断层依赖私有名。这里只做转发、
    不复制判定条件：复制一份等于埋下"白名单与诊断结论不一致"的隐患，
    那恰好是这个 tool 要消灭的问题。

    Args:
        base: 已解析为绝对路径的候选根目录

    Returns:
        base 是文件系统根或用户家目录本身时返回 True

    Example:
        >>> is_base_too_broad(Path(Path.cwd().anchor))
        True
    """
    return _is_too_broad(base)


def write_target_denied(target_path: Path) -> bool:
    """判断配置文件的写入落点是否挂在过宽的宿主目录下

    读边界与写边界不是同一个问题：`<盘符根>/.jenkins-config` 是个窄目录，
    因此可以进 allowed_config_bases() 被读取；但"在磁盘根目录里凭空长出一个
    配置目录"不该发生。这道判定只服务写入方（init_config / save_config），
    判定条件仍转发 _is_too_broad，不复制一份——两处各写一遍时，
    只要有一处放宽，另一处就会静默不一致。

    只对 `<宿主>/.jenkins-config` 形态的落点生效：顶层落点（`<base>/jenkins-config.yaml`）
    由白名单本身把关（文件系统根与家目录本身已被剔除），在这里再判一次会把
    JENKINS_MCP_CONFIG 显式指定的可信文件一并挡掉。落点恰为用户级目录时一律放行，
    那正是期望位置；落点恰为 JENKINS_MCP_CONFIG 指向的那个文件时同样放行——
    读路径已按"部署方设定即可信"精确放行它，写路径再挡一次就成了
    "读得却写不得"，而给出的出路（迁到用户级目录）恰好违背部署方的显式设定。

    Args:
        target_path: 目标配置文件路径（不必已存在）

    Returns:
        落点所在的宿主目录过宽时返回 True

    Example:
        >>> write_target_denied(Path.cwd() / "jenkins-config.yaml")
        False
    """
    target_dir = target_path.parent.resolve()
    if target_dir.name != APP_DIR_NAME:
        return False
    from_env = env_config_file()
    if from_env is not None and target_path.resolve() == from_env.resolve():
        return False
    try:
        if target_dir == user_config_dir().resolve():
            return False
    except RuntimeError:
        # 家目录不可解析时无从比较，继续按宿主目录判定
        pass
    return _is_too_broad(target_dir.parent)


def write_target_denied_steps(first_step: str) -> list[str]:
    """给出"写入落点被拒"时的下一步动作清单

    与 write_target_denied() 放在同一处：判定和出路本就是一件事，
    各调用点各内联一份时，改了文案的那一处会和其余几处静默不一致。
    只有首条动作因调用场景而异（init_config 可改 target，save_config 只能迁文件），
    其余两条恒定。

    Args:
        first_step: 该调用场景下最直接的出路，作为清单首条

    Returns:
        三条可执行动作组成的列表

    Example:
        >>> len(write_target_denied_steps("改用 target='user'"))
        3
    """
    return [
        first_step,
        f"或在客户端 mcp.json 的 env 中把目标目录追加到 {CONFIG_ROOTS_ENV_VAR}",
        "调用 where_config 查看当前允许的根目录列表",
    ]


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


def probe_report_for_mcp(config_path: str = "") -> dict[str, Any]:
    """在 paths.probe_report 之上补齐 MCP 侧特有的锚定信息

    paths 层刻意不认识 JENKINS_MCP_CONFIG（否则该变量会顺带改掉 CLI 的探测），
    所以"环境变量生效了吗"只能在这一层折算：把环境变量折成 probe_report 的入参，
    再把 source 从 explicit_arg 改写为 env_var。相对路径的忽略逻辑完全复用
    paths.env_config_file()——在这里重判一次，就会出现"诊断说生效、实际没生效"。

    同理，白名单的"过宽"过滤也只转发 is_base_too_broad，不复制判定条件。
    注意本函数只做路径层面的探测，不加载配置内容，因此返回体天然不含任何凭据。

    Args:
        config_path: 显式指定的配置文件路径，为空时按环境变量 / 自动探测

    Returns:
        probe_report() 的返回体，额外含 env_var（name / value / effective）、
        allowed_config_bases、path_allowed（解析结果是否落在白名单内），
        且 bases 每项追加 allowed；被"过宽"过滤剔除的候选
        其 skipped_reason 记为 too_broad

    Example:
        >>> probe_report_for_mcp()["env_var"]["name"]
        'JENKINS_MCP_CONFIG'
    """
    from_env = env_config_file()
    env_applies = not config_path and from_env is not None

    report = probe_report(str(from_env) if env_applies else config_path)
    if env_applies:
        report["source"] = "env_var"

    report["env_var"] = {
        "name": CONFIG_ENV_VAR,
        "value": os.environ.get(CONFIG_ENV_VAR, ""),
        "effective": report["source"] == "env_var",
    }

    for item in report["bases"]:
        base = item["base"]
        if base is None:
            item["allowed"] = False
        elif is_base_too_broad(Path(base).resolve()):
            item["allowed"] = False
            item["skipped_reason"] = "too_broad"
        else:
            item["allowed"] = True

    report["allowed_config_bases"] = [str(base) for base in allowed_config_bases()]
    report["path_allowed"] = _path_allowed(str(from_env) if env_applies else config_path)
    return report


def _path_allowed(config_path: str) -> bool:
    """判断解析后的配置路径是否落在允许范围内

    判定完全委托 resolve_config_path（捕获它抛的 PermissionError），不在这里
    重跑一遍 _is_within：诊断层若自带一套判定，就会出现"where_config 说允许、
    真正读配置时被拒"这种自相矛盾的结论，而白名单是安全边界，两套判定意味着
    其中一套迟早失效。

    Args:
        config_path: 已折算环境变量后的路径入参，为空时表示自动探测

    Returns:
        路径在允许范围内时返回 True；被白名单拒绝时返回 False

    Example:
        >>> _path_allowed("")
        True
    """
    try:
        resolve_config_path(config_path)
        return True
    except PermissionError:
        return False


def backup_config_file(target: Path) -> str:
    """把已存在的配置文件备份一份并返回备份路径

    首个备份用 `<name>.bak`；该名字已被占用时改用 `<name>.<时间戳>.bak`——
    固定名意味着第二次覆写会把上一份备份也换成模板内容，而配置文件里
    通常就是唯一一份可用凭据，那样等于没有备份。

    **调用方应在持有 target 的 file_lock 期间调用**：备份与写入落在同一个临界区，
    才不会出现"把已经被替换过的文件备份成原文件"。init_config 即如此使用；
    save_config 是例外——它的写入由 config_io.save_config 内部自行加同名锁，
    外层再套一层会自锁，因此那里只能接受这一小段窗口。


    Args:
        target: 目标配置文件路径

    Returns:
        备份文件的绝对路径；目标不存在（无需备份）时返回空字符串

    Raises:
        OSError: 读取原文件或写入备份失败

    Example:
        >>> backup_config_file(Path("not-exists.yaml"))
        ''
    """
    if not target.exists():
        return ""
    backup_path = target.parent / f"{target.name}.bak"
    if backup_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = target.parent / f"{target.name}.{stamp}.bak"
    backup_path.write_bytes(target.read_bytes())
    return str(backup_path)


@dataclass
class ConfigInspection:
    """配置分层探测结果（路径 → 存在 → 可读 → 可解析 → 完整）

    每一层都单独留一个布尔字段，而不是只给一个 ok/error：doctor 需要按层
    报出"卡在哪一步"，把它们压成单个布尔后就再也分不出"文件不存在"和
    "文件在但没填"，而这两者的下一步动作完全不同。
    """

    config_path: str
    source: str
    exists: bool
    readable: bool
    parse_ok: bool
    complete: bool
    path_allowed: bool
    placeholder_fields: list[str] = field(default_factory=list)
    error_code: str = ""
    error: str = ""
    config: Any | None = None


def _dotted_value(config: Any, dotted: str) -> Any:
    """按点分路径取 Config 上的属性值

    Args:
        config: Config 实例
        dotted: 点分路径，如 'server.url'

    Returns:
        属性值；路径上任一段不存在时返回 None

    Example:
        >>> _dotted_value(None, "server.url") is None
        True
    """
    current = config
    for part in dotted.split("."):
        current = getattr(current, part, None)
        if current is None:
            return None
    return current


def inspect_config(config_path: str = "") -> ConfigInspection:
    """分层探测配置状态：路径白名单 → 存在 → 可读 → 可解析 → 完整

    任何一层失败即短路返回，**不抛异常**：调用方是 doctor 与各 tool 的失败分支，
    它们需要的是"卡在第几层"，而不是再接一层 try。

    "完整"只能靠占位符比对判定：模板里的 http://your-jenkins-server:8080 /
    your-api-token 都非空，能通过 config_io._validate_config，所以一份只 init 过
    没填过的配置照样加载成功（见 config_io.PLACEHOLDER_VALUES 注释）。

    可读性这一层会把文件读一遍，随后 Config.load 又读一遍。多一次读的代价换来
    "权限问题"与"语法问题"在错误码上彻底分开；合并成一次读则只能靠异常类型猜，
    而 Windows/Linux 对"读目录"抛的异常类型本就不同。

    Args:
        config_path: 显式指定的配置文件路径，为空时按环境变量 / 自动探测

    Returns:
        ConfigInspection 实例；error_code 为 '' 表示五层全通过

    Example:
        >>> inspect_config("/not/exists/jenkins-config.yaml").exists
        False
    """
    from jenkins_config.config import Config

    try:
        report = probe_report_for_mcp(config_path)
        resolved, source = report["config_path"], report["source"]
    except Exception as exc:  # 探测本身失败（如家目录不可解析）
        return ConfigInspection(
            config_path="", source="unknown", exists=False, readable=False,
            parse_ok=False, complete=False, path_allowed=False,
            error_code=classify(exc, "resolve"), error=str(exc),
        )

    def _fail(code: str, error: str, **flags: Any) -> ConfigInspection:
        """按当前路径信息组装短路结果"""
        base: dict[str, Any] = {
            "exists": False, "readable": False, "parse_ok": False,
            "complete": False, "path_allowed": True,
        }
        base.update(flags)
        return ConfigInspection(
            config_path=resolved, source=source, error_code=code, error=error, **base
        )

    try:
        resolved = resolve_config_path(config_path)
    except PermissionError as exc:
        return _fail(ErrorCode.CONFIG_PATH_DENIED, str(exc), path_allowed=False)
    except RuntimeError as exc:
        return _fail(ErrorCode.HOME_UNAVAILABLE, str(exc), path_allowed=False)

    path = Path(resolved)
    if not path.is_file():
        return _fail(ErrorCode.CONFIG_NOT_FOUND, f"配置文件不存在: {resolved}")

    try:
        path.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return _fail(
            classify(exc, "read"), f"配置文件不可读: {resolved}（{exc}）", exists=True
        )

    try:
        config = Config.load(resolved)
    except Exception as exc:
        return _fail(
            classify(exc, "parse"), f"配置解析失败: {exc}", exists=True, readable=True
        )

    placeholders = [
        key
        for key, placeholder in PLACEHOLDER_VALUES.items()
        if _dotted_value(config, key) == placeholder
    ]
    if placeholders:
        return ConfigInspection(
            config_path=resolved, source=source, exists=True, readable=True,
            parse_ok=True, complete=False, path_allowed=True,
            placeholder_fields=placeholders,
            error_code=ErrorCode.CONFIG_INCOMPLETE,
            # 只报键名，不回显取值：这里含 server.token
            error=f"配置仍是模板占位符，未填写的字段: {', '.join(placeholders)}",
            config=config,
        )

    return ConfigInspection(
        config_path=resolved, source=source, exists=True, readable=True,
        parse_ok=True, complete=True, path_allowed=True, config=config,
    )


def config_failure_payload(
    config_path: str = "", exc: BaseException | None = None, action: str = ""
) -> dict[str, Any]:
    """把配置相关的异常折算为统一失败载荷

    先重跑一次路径解析来确定 phase：白名单越界与文件权限失败都抛 PermissionError，
    而 tool 内部只拿得到一个异常对象，靠类型分不出来。重跑只做存在性判断、
    不读文件内容，代价可忽略，换来错误码的确定性。

    Args:
        config_path: 调用方传入的配置文件路径，为空时按环境变量 / 自动探测
        exc: 捕获到的异常，为 None 时按 UNKNOWN 处理
        action: 人类可读的动作前缀，如 "加载配置失败"

    Returns:
        failure_payload() 的五字段字典

    Example:
        >>> config_failure_payload(exc=FileNotFoundError("x"))["error_code"]
        'config_not_found'
    """
    def _describe(reason: BaseException) -> str:
        """拼接 "动作: 原因" 形式的文案"""
        return f"{action}: {reason}" if action else str(reason)

    try:
        resolved = resolve_config_path(config_path)
    except PermissionError as denied:
        return failure_payload(ErrorCode.CONFIG_PATH_DENIED, _describe(denied))
    except RuntimeError as no_home:
        return failure_payload(ErrorCode.HOME_UNAVAILABLE, _describe(no_home))

    if exc is None:
        return failure_payload(ErrorCode.UNKNOWN, action or "未知错误", resolved)
    return failure_payload(classify(exc, "read"), _describe(exc), resolved)


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


def failure_result(
    message: str, job_key: str = "", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """构造 trigger_build / rebuild_last 的失败返回体

    容器结构保持 {'triggered': [], 'failed': [...]} 不变——调用方（含既有测试）
    都按这个形状读结果。可行动信息以 error_code / next_steps 等键**追加在顶层**，
    而不是塞进 failed[0]：失败可能有多条，顶层只需要一份"这次为什么整体失败"。
    failed[0]['error'] 继续保留人类可读文案。

    Args:
        message: 错误信息（人类可读）
        job_key: 相关的 Job 标识，未知时留空
        payload: errors.failure_payload() 的返回体，非 None 时其五个字段合并到顶层

    Returns:
        含空 triggered 列表与单条 failed 记录的字典；带 payload 时额外含
        error_code、error、config_path、next_steps、docs

    Example:
        >>> failure_result("配置文件不存在")
        {'triggered': [], 'failed': [{'job_key': '', 'error': '配置文件不存在'}]}
    """
    result: dict[str, Any] = {
        "triggered": [],
        "failed": [{"job_key": job_key, "error": message}],
    }
    if payload:
        result.update(payload)
    return result
