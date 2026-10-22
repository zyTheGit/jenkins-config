"""
MCP Tools - 构建操作工具

提供触发构建和重建上次构建的操作类 Tool。
核心设计：触发后快速返回，不等待构建完成，避免 MCP 调用超时。
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from jenkins_config.build_flow import QueueOutcome, select_jobs, trigger_queued
from jenkins_config.mcp.errors import ErrorCode, config_failure_payload, failure_payload
from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import (
    failure_result,
    get_config,
    history_manager,
    host_allowed,
    resolve_history_path,
    write_allowed,
    write_denied_message,
)
from jenkins_config.utils import log_warn

# 构建编号探测的整体时间预算上限（秒），保持触发接口快速返回的语义
MAX_PROBE_BUDGET = 15


def _queue_payload(outcome: QueueOutcome) -> dict[str, Any]:
    """把队列触发结果渲染为 MCP 返回结构

    Args:
        outcome: build_flow.trigger_queued 返回的结果

    Returns:
        包含 triggered、failed 列表的字典；历史写入失败时额外带 history_error 字段

    Example:
        >>> _queue_payload(QueueOutcome())
        {'triggered': [], 'failed': []}
    """
    triggered: list[dict[str, Any]] = []
    for item in outcome.triggered:
        entry: dict[str, Any] = {
            "job_key": item.job_key,
            "queue_url": item.queue_url,
            "build_num": item.build_num,
            # MCP 触发写入的历史为 BUILDING 占位记录，不会自动更新为终态，
            # 显式提示调用方通过 get_build_status 查询真实结果
            "note": "构建状态为占位记录，实际结果请用 get_build_status 查询",
        }
        if not item.build_num:
            entry["note"] = (
                "构建已入队，编号尚未分配（历史记录 build_num=0，不参与重建分组）；"
                "构建状态为占位记录，实际结果请用 get_build_status 查询"
            )
        triggered.append(entry)

    result: dict[str, Any] = {
        "triggered": triggered,
        "failed": [
            {"job_key": item.job_key, "error": item.error}
            for item in outcome.failed
        ],
    }
    if outcome.history_error:
        # 历史写入失败不影响触发结果，但必须让调用方可见：
        # 否则 rebuild_last 会查不到刚触发的记录
        message = f"构建已触发，但历史写入失败: {outcome.history_error}"
        log_warn(message)
        result["history_error"] = message
    return result


def _trigger_jobs(
    config: Any,
    jobs: list[Any],
    history_file: str = "",
    wait_build_num: bool = True,
) -> dict[str, Any]:
    """触发一组构建并快速返回

    对每个 Job 执行触发操作，并发探测构建编号（短超时），不等待构建完成。

    Args:
        config: Config 实例
        jobs: Job 列表
        history_file: 历史文件路径，非空时将成功触发的记录写入历史
        wait_build_num: 为 False 时跳过编号探测，仅返回 queue_url

    Returns:
        包含 triggered 和 failed 两个列表的字典

    Example:
        >>> _trigger_jobs(None, [])  # doctest: +SKIP
        {'triggered': [], 'failed': []}
    """
    # 编号探测预算取 queue_timeout 与 MAX_PROBE_BUDGET 的较小值，
    # 覆盖 Jenkins 队列默认 5 秒静默期，避免落盘 build_num=0 的脏记录；
    # 因为改为并发探测，该预算同时是整批的大致墙钟耗时上限。
    queue_timeout = getattr(config.build, "queue_timeout", 30)
    if isinstance(queue_timeout, int):
        probe_timeout = min(queue_timeout, MAX_PROBE_BUDGET)
    else:
        probe_timeout = MAX_PROBE_BUDGET

    from jenkins_config.jenkins import JenkinsClient

    # 统一用 closing 收口客户端释放，避免手写 try/finally
    with closing(JenkinsClient.from_config(config)) as client:
        outcome = trigger_queued(
            client,
            jobs,
            history_file=history_file,
            probe_timeout=probe_timeout,
            branch_field_for=config.branch_field_for,
            wait_build_num=wait_build_num,
        )
    return _queue_payload(outcome)


@mcp.tool()
def trigger_build(
    env: str = "",
    projects: str = "",
    branch: str = "",
    params: str = "",
    wait_build_num: bool = True,
    config_path: str = "",
) -> dict[str, Any]:
    """触发 Jenkins 构建（触发后快速返回，不等待构建完成）

    写操作，需先设置环境变量 JENKINS_MCP_ALLOW_WRITE=1 才会执行。

    Args:
        env: 环境名称，不能为空
        projects: 逗号分隔的项目名称，为空时触发该环境下所有项目
        branch: 自定义分支名，覆盖配置中的分支参数
        params: 额外构建参数，支持 JSON 格式或 key=value&key=value 格式
        wait_build_num: 是否等待 Jenkins 分配构建编号（默认等待，最多约 15 秒）；
            置为 False 时立即返回，仅带 queue_url
        config_path: 配置文件路径，为空时自动检测

    Returns:
        包含 triggered（已触发列表）和 failed（失败列表）的字典，
        每项包含 job_key、queue_url、build_num 等字段；
        参数中含非标量值时附带 skipped_params，历史写入失败时附带 history_error；
        整体失败时顶层追加 error_code / config_path / next_steps / docs

    Example:
        >>> trigger_build(env="dev", projects="project-a")  # doctest: +SKIP
        {'triggered': [{'job_key': 'dev_project_a', ...}], 'failed': []}
    """
    try:
        if not write_allowed():
            message = write_denied_message("触发构建")
            return failure_result(
                message, payload=failure_payload(ErrorCode.WRITE_NOT_ALLOWED, message)
            )

        if not env:
            message = "必须指定环境名称 (env 参数不能为空)"
            return failure_result(
                message, payload=failure_payload(ErrorCode.INVALID_TARGET, message)
            )

        config = get_config(config_path)

        # 解析项目过滤列表
        jobs_filter = None
        if projects:
            project_names = [p.strip() for p in projects.split(",") if p.strip()]
            if project_names:
                jobs_filter = project_names

        try:
            selection = select_jobs(
                config,
                env=env,
                filters=jobs_filter,
                branch=branch,
                params_text=params,
            )
        except ValueError as e:
            message = f"参数解析失败: {e}"
            return failure_result(
                message, payload=failure_payload(ErrorCode.INVALID_TARGET, message)
            )

        jobs = selection.jobs

        if not jobs:
            message = f"环境 '{env}' 中没有找到匹配的项目"
            return failure_result(
                message, payload=failure_payload(ErrorCode.INVALID_TARGET, message)
            )

        result = _trigger_jobs(
            config,
            jobs,
            str(resolve_history_path(config_path)),
            wait_build_num,
        )
        if selection.skipped_params:
            result["skipped_params"] = selection.skipped_params
        return result

    except FileNotFoundError as e:
        return failure_result(
            f"配置文件不存在: {e}",
            payload=config_failure_payload(config_path, e, "配置文件不存在"),
        )
    except Exception as e:
        return failure_result(
            f"触发构建失败: {e}",
            payload=config_failure_payload(config_path, e, "触发构建失败"),
        )


def _optional_branch_field_for(config_path: str) -> Callable[[str], str] | None:
    """尽力取得按环境解析分支参数名的函数，用于直连模式写历史

    直连模式不要求配置文件存在，但配置可读时应沿用配置里的
    环境级 branch_field，避免历史记录的 branch 字段恒为空。

    Args:
        config_path: 配置文件路径，为空时自动检测

    Returns:
        config.branch_field_for 可调用对象；配置不可用时返回 None（回退默认 "branch"）

    Example:
        >>> _optional_branch_field_for("/not/exists.yaml") is None
        True
    """
    try:
        return get_config(config_path).branch_field_for
    except Exception:
        return None


def _jobs_from_records(last_group: list[Any]) -> tuple[list[Any], list[dict[str, str]]]:
    """从历史记录还原直连模式所需的 Job 信息

    Job 路径优先取记录中持久化的 job_path（可与项目名不同，如 folder/my-job），
    缺失时退回 project_name，再退回从 job_key 推导（含前缀校验）。

    Args:
        last_group: 上次构建分组的 BuildRecord 列表

    Returns:
        (可重建的 Job 列表, 被跳过的记录说明列表) 元组

    Example:
        >>> _jobs_from_records([])
        ([], [])
    """
    from types import SimpleNamespace

    from jenkins_config.config import project_name_from_job_key

    jobs: list[Any] = []
    skipped: list[dict[str, str]] = []
    for record in last_group:
        project_name = record.project_name or project_name_from_job_key(
            record.env, record.job_key
        )
        job_path = getattr(record, "job_path", "") or project_name
        if not job_path:
            # env 为空或前缀不匹配时无法安全推导，显式跳过，
            # 避免静默 no-op 导致推导出错误的 Job 路径
            skipped.append({
                "job_key": record.job_key,
                "error": "无法从 job_key 推导 job_path",
            })
            continue

        jobs.append(SimpleNamespace(
            key=record.job_key,
            path=job_path,
            env=record.env,
            project_name=project_name or job_path,
            params=dict(record.params) if record.params else {},
        ))
    return jobs, skipped


@mcp.tool()
def rebuild_last(
    config_path: str = "",
    jenkins_url: str = "",
    jenkins_token: str = "",
    jenkins_username: str = "",
    history_file: str = "",
) -> dict[str, Any]:
    """重建上次构建的项目（触发后快速返回，不等待构建完成）

    从构建历史中获取上次成功触发的项目列表，重新触发这些项目的构建。
    写操作，需先设置环境变量 JENKINS_MCP_ALLOW_WRITE=1 才会执行。
    支持两种模式：
    1. 配置文件模式：提供 config_path（或自动检测），从配置文件读取 Jenkins 连接信息
    2. 直连模式：提供 jenkins_url + jenkins_token，无需配置文件；
       目标地址必须与配置中的 server.url 同 host，
       或通过环境变量 JENKINS_MCP_ALLOWED_HOSTS（逗号分隔）显式放行

    Args:
        config_path: 配置文件路径，为空时自动检测
        jenkins_url: Jenkins 服务器地址（直连模式，优先级高于 config_path）
        jenkins_token: Jenkins API Token（直连模式）
        jenkins_username: Jenkins 用户名（直连模式，默认 admin）
        history_file: 历史文件路径（直连模式，默认为 data/build_history.json）

    Returns:
        包含 triggered（已触发列表）和 failed（失败列表）的字典，
        格式同 trigger_build；整体失败时顶层追加
        error_code / config_path / next_steps / docs

    Example:
        >>> rebuild_last()  # doctest: +SKIP
        {'triggered': [{'job_key': 'dev_project_a', ...}], 'failed': []}
    """
    try:
        if not write_allowed():
            message = write_denied_message("重建构建")
            return failure_result(
                message, payload=failure_payload(ErrorCode.WRITE_NOT_ALLOWED, message)
            )

        from jenkins_config.jenkins import JenkinsClient

        # 判断是否为直连模式：两个参数必须成对出现，
        # 只给其一时显式报错，避免静默回落到配置文件模式（会跳过主机白名单）
        if bool(jenkins_url) != bool(jenkins_token):
            message = "直连模式必须同时提供 jenkins_url 与 jenkins_token"
            return failure_result(
                message, payload=failure_payload(ErrorCode.INVALID_TARGET, message)
            )

        if jenkins_url and jenkins_token:

            if not host_allowed(jenkins_url):
                message = (
                    f"目标地址不在允许范围内: {jenkins_url}（"
                    "请通过环境变量 JENKINS_MCP_ALLOWED_HOSTS 放行）"
                )
                return failure_result(
                    message,
                    payload=failure_payload(
                        ErrorCode.INVALID_TARGET,
                        message,
                        next_steps=[
                            "把该主机加入环境变量 JENKINS_MCP_ALLOWED_HOSTS（逗号分隔）后重启 Server",
                            "或改用配置文件模式（不传 jenkins_url / jenkins_token）",
                        ],
                    ),
                )

            hist_file = history_file or str(resolve_history_path(config_path))
            if not Path(hist_file).exists():
                message = f"历史文件不存在: {hist_file}"
                return failure_result(
                    message,
                    payload=failure_payload(
                        ErrorCode.INVALID_TARGET,
                        message,
                        next_steps=[
                            "先调用 trigger_build 触发一次构建以生成历史记录",
                            "或调用 doctor 确认 history_path 指向的位置是否正确",
                        ],
                    ),
                )

            manager = history_manager(history_file=hist_file)
            last_group = manager.get_last_build_group()
            if not last_group:
                return failure_result(
                    "没有找到上次成功构建的记录",
                    payload=failure_payload(
                        ErrorCode.INVALID_TARGET,
                        "没有找到上次成功构建的记录",
                        next_steps=[
                            "调用 show_history 查看现有记录",
                            "或调用 trigger_build 触发一次构建后重试",
                        ],
                    ),
                )

            jobs, skipped = _jobs_from_records(last_group)
            if not jobs:
                # 一条都没能还原成 Job：这是整体失败，必须带 error_code /
                # next_steps，否则调用方只能从 failed[0].error 的中文里猜原因
                result = failure_result(
                    "没有可重建的项目",
                    payload=failure_payload(
                        ErrorCode.INVALID_TARGET,
                        "历史记录中的项目都无法还原为可构建对象，没有可重建的项目",
                        next_steps=[
                            "调用 show_history 查看历史记录里的项目名",
                            "调用 list_projects 确认这些项目是否仍然存在",
                            "或调用 trigger_build 显式指定要构建的项目",
                        ],
                    ),
                )
                if skipped:
                    result["failed"] = skipped
                return result


            # 直连模式也尽力取到分支参数名的解析函数：
            # 配置可读时用 config.branch_field_for，否则回退默认 "branch"
            branch_field_for = _optional_branch_field_for(config_path)
            with closing(JenkinsClient(
                url=jenkins_url,
                token=jenkins_token,
                username=jenkins_username or "admin",
            )) as client:
                # 直连模式与配置文件模式行为一致：重建后将触发记录回写历史，
                # 记录中的 env 来自历史记录（已随 SimpleNamespace 传入）
                outcome = trigger_queued(
                    client,
                    jobs,
                    history_file=hist_file,
                    branch_field_for=branch_field_for,
                )
            result = _queue_payload(outcome)
            if skipped:
                result["failed"].extend(skipped)
            return result

        # 配置文件模式
        config = get_config(config_path)
        history_file_path = str(resolve_history_path(config_path))
        manager = history_manager(history_file=history_file_path)

        last_group = manager.get_last_build_group()
        if not last_group:
            return failure_result(
                "没有找到上次成功构建的记录",
                payload=failure_payload(
                    ErrorCode.INVALID_TARGET,
                    "没有找到上次成功构建的记录",
                    history_file_path,
                    [
                        "调用 show_history 查看现有记录",
                        "或调用 trigger_build 触发一次构建后重试",
                    ],
                ),
            )

        jobs = []
        skipped_cfg: list[dict[str, str]] = []
        for record in last_group:
            job = config.create_job_from_record(record)
            if job:
                jobs.append(job)
            else:
                skipped_cfg.append({
                    "job_key": record.job_key,
                    "error": f"项目 '{record.project_name}' 在配置中不存在，跳过",
                })

        if not jobs:
            result = failure_result(
                "没有可重建的项目",
                payload=failure_payload(
                    ErrorCode.INVALID_TARGET,
                    "历史记录中的项目在当前配置里都不存在，没有可重建的项目",
                    history_file_path,
                    [
                        "调用 list_projects 确认这些项目是否仍在配置中",
                        "调用 show_history 查看历史记录里的项目名",
                        "或调用 trigger_build 显式指定要构建的项目",
                    ],
                ),
            )
            if skipped_cfg:
                result["failed"] = skipped_cfg
            return result


        result = _trigger_jobs(config, jobs, history_file_path)
        if skipped_cfg:
            result["failed"].extend(skipped_cfg)
        return result

    except FileNotFoundError as e:
        return failure_result(
            f"文件不存在: {e}",
            payload=config_failure_payload(config_path, e, "文件不存在"),
        )
    except Exception as e:
        return failure_result(
            f"重建失败: {e}",
            payload=config_failure_payload(config_path, e, "重建失败"),
        )
