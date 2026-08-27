"""
MCP Tools - 本地体检（doctor）

回答"我这套 MCP 配置现在能不能正常干活、哪一步坏了、下一步做什么"。
与 where_config 的分工：后者只答"读的是哪份配置"，doctor 把配置、写开关、
主机白名单、历史文件、日志落点、运行模式一次性摊开成固定 11 项检查。
与 health_check 的分工：health_check 打 Jenkins，doctor 默认**不发任何网络请求**
（include_jenkins=true 时才追加一项），因此在断网 / 未配好凭据时依然可用。

脱敏靠"只报键名 + 已配置 / 未配置"：任何情况下都不把 token、password 的原始
字符放进返回体，也不回显 server.url 之外的连接细节。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from jenkins_config.mcp.server import current_log_sinks, mcp
from jenkins_config.mcp.utils import (
    ALLOWED_HOSTS_ENV_VAR,
    CONFIG_ROOTS_ENV_VAR,
    WRITE_ENV_VAR,
    ConfigInspection,
    inspect_config,
    jenkins_client,
    resolve_history_path,
    write_allowed,
)
from jenkins_config.paths import runtime_mode


# 严重程度排序：整体 status 取所有参与项中最严重者
SEVERITY = {"skip": 0, "ok": 1, "warn": 2, "error": 3}

# 纯信息项，不参与整体 status 升级（runtime_mode 恒为 ok，显式列出以免日后改动时漂移）
INFO_ONLY_CHECKS = ("runtime_mode",)

# 配置类检查项的固定顺序（短路时按此顺序补 skip）
CONFIG_CHECK_NAMES = (
    "config_located",
    "config_readable",
    "config_parsable",
    "config_complete",
)


def _check(name: str, status: str, detail: str, hint: str = "") -> dict[str, Any]:
    """组装单个检查项

    Args:
        name: 检查项名称
        status: ok / warn / error / skip
        detail: 检查结论的具体说明（不含任何凭据原文）
        hint: 修复建议，ok 时留空

    Returns:
        含 name / status / detail / hint 的字典

    Example:
        >>> _check("write_gate", "ok", "写操作已放开")["status"]
        'ok'
    """
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def _credential_detail(inspection: ConfigInspection) -> str:
    """描述凭据字段的填写情况（只报键名与是否配置）

    Args:
        inspection: 配置分层探测结果

    Returns:
        形如 "server.url: 已配置；server.token: 已配置" 的说明

    Example:
        >>> "server.token" in _credential_detail(inspect_config("/nope.yaml"))
        True
    """
    server = getattr(inspection.config, "server", None)
    parts = []
    for key in ("url", "token"):
        value = getattr(server, key, "") or ""
        parts.append(f"server.{key}: {'已配置' if str(value).strip() else '未配置'}")
    detail = "；".join(parts)
    if inspection.placeholder_fields:
        detail += f"；仍为模板占位符: {', '.join(inspection.placeholder_fields)}"
    return detail


def _config_checks(inspection: ConfigInspection) -> list[dict[str, Any]]:
    """把配置分层探测结果翻译为 5 个检查项

    某一层失败后，其下游一律记 skip 而不是重复报 error：同一个根因报三次
    只会让调用方分不清该先修哪个。

    Args:
        inspection: 配置分层探测结果

    Returns:
        依次为 config_located、config_readable、config_parsable、
        config_complete、config_path_allowed 的检查项列表

    Example:
        >>> [item["name"] for item in _config_checks(inspect_config())][:1]
        ['config_located']
    """
    from jenkins_config.mcp.errors import ErrorCode

    path_allowed = _check(
        "config_path_allowed",
        "ok" if inspection.path_allowed else "error",
        f"配置路径在允许范围内: {inspection.config_path}"
        if inspection.path_allowed
        else inspection.error,
        "" if inspection.path_allowed
        else f"把该目录追加到环境变量 {CONFIG_ROOTS_ENV_VAR}，或改用 where_config 报出的允许目录",
    )
    if not inspection.path_allowed:
        skipped = [
            _check(name, "skip", "配置路径未通过白名单校验，本项跳过")
            for name in CONFIG_CHECK_NAMES
        ]
        return skipped + [path_allowed]

    located_ok = inspection.exists and inspection.source != "fallback"
    checks = [
        _check(
            "config_located",
            "ok" if located_ok else "error",
            f"命中配置文件: {inspection.config_path}（来源 {inspection.source}）"
            if located_ok
            else f"未找到配置文件: {inspection.config_path}（来源 {inspection.source}）",
            "" if located_ok
            else "调用 init_config 生成配置模板，或调用 where_config 查看候选目录顺序",
        )
    ]
    if not located_ok:
        checks += [
            _check(name, "skip", "配置文件不存在，本项跳过")
            for name in CONFIG_CHECK_NAMES[1:]
        ]
        return checks + [path_allowed]

    checks.append(_check(
        "config_readable",
        "ok" if inspection.readable else "error",
        "配置文件可读" if inspection.readable else inspection.error,
        "" if inspection.readable else "确认该路径是文件而非目录，并补齐当前用户的读权限",
    ))
    if not inspection.readable:
        checks += [
            _check(name, "skip", "配置文件不可读，本项跳过")
            for name in CONFIG_CHECK_NAMES[2:]
        ]
        return checks + [path_allowed]

    checks.append(_check(
        "config_parsable",
        "ok" if inspection.parse_ok else "error",
        "配置解析成功，Config 已构造"
        if inspection.parse_ok
        else f"{inspection.error}（文件: {inspection.config_path}）",
        "" if inspection.parse_ok else "按上面的报错修正该文件的语法或必填字段",
    ))

    if inspection.parse_ok:
        complete_status = "ok" if inspection.complete else "warn"
    elif inspection.error_code == ErrorCode.CONFIG_INCOMPLETE:
        # server.url / server.token 为空、项目缺 name：属"没填完"而非"语法坏"
        complete_status = "error"
    else:
        complete_status = "skip"

    if complete_status == "skip":
        checks.append(_check("config_complete", "skip", "配置解析失败，本项跳过"))
    elif complete_status == "ok":
        checks.append(_check("config_complete", "ok", _credential_detail(inspection)))
    else:
        checks.append(_check(
            "config_complete",
            complete_status,
            f"{_credential_detail(inspection)}；{inspection.error}",
            "编辑配置文件，把 server.url / server.token 填为真实取值",
        ))
    return checks + [path_allowed]


def _env_checks() -> list[dict[str, Any]]:
    """检查写开关与主机白名单两个环境变量

    两者未设置都只判 warn：只读模式和"退回配置文件 server.url"都是**有意的默认值**，
    不是故障。把它们判成 error 会让 doctor 在正常只读场景下永远报红，
    调用方很快就会忽略整体 status。

    Returns:
        write_gate 与 allowed_hosts 两个检查项

    Example:
        >>> [item["name"] for item in _env_checks()]
        ['write_gate', 'allowed_hosts']
    """
    hosts = os.environ.get(ALLOWED_HOSTS_ENV_VAR, "").strip()
    return [
        _check(
            "write_gate",
            "ok" if write_allowed() else "warn",
            f"{WRITE_ENV_VAR} 已开启，trigger_build / rebuild_last / save_config 可执行"
            if write_allowed()
            else f"{WRITE_ENV_VAR} 未开启，当前为只读模式（写类 tool 会被拒绝）",
            "" if write_allowed() else f"需要写操作时在客户端 env 中设置 {WRITE_ENV_VAR}=1",
        ),
        _check(
            "allowed_hosts",
            "ok" if hosts else "warn",
            f"{ALLOWED_HOSTS_ENV_VAR} 已显式设置: {hosts}"
            if hosts
            else f"{ALLOWED_HOSTS_ENV_VAR} 未设置，直连模式退回配置文件 server.url 为唯一允许主机",
            "" if hosts else f"需要严格限定目标主机时设置 {ALLOWED_HOSTS_ENV_VAR}（逗号分隔）",
        ),
    ]


def _first_existing_dir(path: Path) -> Path | None:
    """向上找到第一个已存在的祖先目录

    Args:
        path: 起始目录（可能尚不存在）

    Returns:
        最近的已存在祖先目录；一个都没有时返回 None

    Example:
        >>> _first_existing_dir(Path.cwd()) == Path.cwd()
        True
    """
    for candidate in [path, *path.parents]:
        if candidate.is_dir():
            return candidate
    return None


def _history_check(config_path: str) -> dict[str, Any]:
    """检查构建历史文件的可用性（只读，绝不创建目录）

    沿用 utils.history_manager(create=False) 的"只读无副作用"约定：
    体检本身不该在用户磁盘上留下目录。因此"文件还不存在但目录可创建"判 ok
    而不是 warn——全新安装还没触发过构建时本就没有历史文件，那不是故障；
    warn 只留给"确实存在但说不清能不能用"的边角情形。

    Args:
        config_path: 配置文件路径，为空时自动探测

    Returns:
        history_path 检查项

    Example:
        >>> _history_check("")["name"]
        'history_path'
    """
    try:
        history = resolve_history_path(config_path)
    except Exception as exc:
        return _check(
            "history_path", "warn", f"历史文件路径无法解析: {exc}",
            "先修复配置路径问题（见 config_path_allowed / config_located）",
        )

    if history.is_file():
        if os.access(history, os.R_OK):
            return _check("history_path", "ok", f"历史文件可读: {history}")
        return _check(
            "history_path", "error", f"历史文件存在但不可读: {history}",
            "补齐该文件的读权限",
        )

    parent = history.parent
    if parent.is_dir():
        if os.access(parent, os.W_OK):
            return _check(
                "history_path", "ok",
                f"历史文件尚不存在，首次触发构建时会创建: {history}",
            )
        return _check(
            "history_path", "error", f"历史目录不可写: {parent}",
            "补齐该目录的写权限，或把配置文件放到可写目录",
        )

    ancestor = _first_existing_dir(parent)
    if ancestor is None:
        return _check(
            "history_path", "warn",
            f"历史目录 {parent} 不存在，且找不到任何已存在的祖先目录",
            "确认配置文件所在磁盘 / 挂载点可用",
        )
    if os.access(ancestor, os.W_OK):
        return _check(
            "history_path", "ok",
            f"历史文件与其目录尚不存在，首次写入时会创建 {parent}",
        )
    return _check(
        "history_path", "error",
        f"历史目录无法创建: {parent}（最近的已存在祖先 {ancestor} 不可写）",
        "把配置文件放到可写目录，或补齐该路径的写权限",
    )


def _log_check() -> dict[str, Any]:
    """报出当前日志等级与实际落点

    只判 ok / warn：日志落点异常不影响 JSON-RPC 会话（stderr 始终可用），
    因此即使 JENKINS_MCP_LOG_FILE 指向不可写路径也只判 warn。
    任何探查失败都降级为 warn 并附上环境变量取值，绝不抛出。

    Returns:
        log_sink 检查项

    Example:
        >>> _log_check()["name"]
        'log_sink'
    """
    from jenkins_config.mcp.server import LOG_FILE_ENV_VAR

    sinks = current_log_sinks()
    wanted_file = bool(sinks["env"].get(LOG_FILE_ENV_VAR, "").strip())
    detail = (
        f"级别 {sinks['level']}；落点: {', '.join(sinks['sinks'])}；"
        f"{LOG_FILE_ENV_VAR}={sinks['env'].get(LOG_FILE_ENV_VAR) or '(未设置)'}"
    )
    if any("探查失败" in item for item in sinks["sinks"]):
        return _check("log_sink", "warn", detail, "查看 stderr 输出确认日志是否正常")
    if wanted_file and sinks["initialized"] and not sinks["file_sinks"]:
        return _check(
            "log_sink", "warn",
            f"{detail}；已请求文件日志但未安装文件 handler（路径不可写时会降级为仅 stderr）",
            "换一个可写的 JENKINS_MCP_LOG_FILE 路径，或设为 auto 落到用户级日志目录",
        )
    return _check("log_sink", "ok", detail)


def _runtime_check() -> dict[str, Any]:
    """报出运行模式、进程 CWD 与版本（恒为 ok）

    这项永远不判故障：它存在的意义是让"同一台机器上跑的是哪个副本"可追溯，
    并且刻意不参与整体 status 升级（见 INFO_ONLY_CHECKS）。

    Returns:
        runtime_mode 检查项

    Example:
        >>> _runtime_check()["status"]
        'ok'
    """
    try:
        from importlib.metadata import version

        pkg_version = version("jenkins-config")
    except Exception:
        pkg_version = "unknown"

    # 运行形态判定统一走 paths.runtime_mode()：与 probe_report 的 mode
    # 同源，避免诊断口径和真实探测顺序各说一套
    mode = runtime_mode()
    return _check(
        "runtime_mode", "ok",
        f"运行模式 {mode}；版本 {pkg_version}；进程 CWD: {Path.cwd()}",
    )


def _jenkins_check(config_path: str, include_jenkins: bool) -> dict[str, Any]:
    """按需检测 Jenkins 连通性（默认跳过，不发任何网络请求）

    默认 skip 而不是默默去连：doctor 要在断网、凭据未配好的环境里也能秒回，
    而且体检不该产生对外请求这类副作用。

    Args:
        config_path: 配置文件路径，为空时自动探测
        include_jenkins: 为 True 时才真正发起健康检查

    Returns:
        jenkins_reachable 检查项（附 URL，不附凭据）

    Example:
        >>> _jenkins_check("", False)["status"]
        'skip'
    """
    if not include_jenkins:
        return _check(
            "jenkins_reachable", "skip",
            "未启用 Jenkins 连通性检测（默认不发网络请求）",
            "需要检测时传 include_jenkins=true，或调用 health_check",
        )
    try:
        with jenkins_client(config_path) as client:
            url = getattr(client, "base_url", "")
            reachable = client.health_check()
    except Exception as exc:
        return _check(
            "jenkins_reachable", "error", f"连通性检测失败: {exc}",
            "确认 server.url 可达、凭据有效；必要时调用 health_check 复查",
        )
    if reachable:
        return _check("jenkins_reachable", "ok", f"Jenkins 可达: {url}")
    return _check(
        "jenkins_reachable", "error", f"Jenkins 不可达: {url}",
        "确认该地址可访问、凭据有效（token 是否过期）",
    )


def _overall_status(checks: list[dict[str, Any]]) -> str:
    """取所有检查项中最严重的状态作为整体结论

    runtime_mode 这类纯信息项不参与升级（见 INFO_ONLY_CHECKS）；
    warn 永远不会把整体拉到 error，因此只读模式不会被报成故障。

    Args:
        checks: 检查项列表

    Returns:
        ok / warn / error

    Example:
        >>> _overall_status([_check("write_gate", "warn", "")])
        'warn'
    """
    worst = "ok"
    for item in checks:
        if item["name"] in INFO_ONLY_CHECKS:
            continue
        if SEVERITY.get(item["status"], 0) > SEVERITY[worst]:
            worst = item["status"]
    return worst if worst in ("ok", "warn", "error") else "ok"


def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    """统计各状态的检查项数量

    Args:
        checks: 检查项列表

    Returns:
        含 ok / warn / error / skip 四个计数的字典

    Example:
        >>> _summary([_check("write_gate", "ok", "")])["ok"]
        1
    """
    counts = {"ok": 0, "warn": 0, "error": 0, "skip": 0}
    for item in checks:
        if item["status"] in counts:
            counts[item["status"]] += 1
    return counts


def _collect_next_steps(checks: list[dict[str, Any]]) -> list[str]:
    """汇总失败项的修复建议，error 优先于 warn

    Args:
        checks: 检查项列表

    Returns:
        去重后的下一步动作列表；整体正常时为空列表

    Example:
        >>> _collect_next_steps([_check("write_gate", "ok", "")])
        []
    """
    steps: list[str] = []
    for wanted in ("error", "warn"):
        for item in checks:
            if item["status"] != wanted or not item["hint"]:
                continue
            if item["hint"] not in steps:
                steps.append(item["hint"])
    return steps


@mcp.tool()
def doctor(config_path: str = "", include_jenkins: bool = False) -> dict[str, Any]:
    """本地体检：一次性报出配置、权限、历史、日志、运行模式的健康状况

    默认不发起任何 Jenkins 网络请求，因此断网或凭据未配好时同样可用。
    返回体只以"键名 + 已配置 / 未配置"描述凭据，不含 token / password 原文。

    Args:
        config_path: 配置文件路径，为空时按环境变量 / 自动探测
        include_jenkins: 为 True 时追加一次 Jenkins 连通性检测（会发网络请求）

    Returns:
        含 status（ok / warn / error）、checks（11 项，每项 name / status /
        detail / hint）、summary（各状态计数）、config_path、next_steps 的字典

    Example:
        >>> doctor()["summary"]["error"]  # doctest: +SKIP
        0
    """
    inspection = inspect_config(config_path)
    checks = _config_checks(inspection)
    checks += _env_checks()
    checks.append(_history_check(config_path))
    checks.append(_log_check())
    checks.append(_runtime_check())
    checks.append(_jenkins_check(config_path, include_jenkins))

    status = _overall_status(checks)
    next_steps = _collect_next_steps(checks) if status != "ok" else []
    if status != "ok" and not next_steps:
        # 契约要求 status != ok 时 next_steps 非空，兜一条通用动作
        next_steps = ["调用 where_config 确认配置来源，再按 checks 中的 detail 逐项处理"]

    return {
        "status": status,
        "checks": checks,
        "summary": _summary(checks),
        "config_path": inspection.config_path,
        "next_steps": next_steps,
    }
