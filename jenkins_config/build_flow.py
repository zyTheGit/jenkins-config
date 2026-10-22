# jenkins_config/build_flow.py
"""
构建流程模块 - CLI 与 MCP Server 共享的构建流程规则

调用方负责配置加载、确认/写入门禁与结果渲染；流程规则（环境与项目筛选、
分支覆写、参数合并、参数文本解析）只在本模块实现一次。
"""

from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .build_errors import extract_error_lines, save_error_log
from .build_result import BuildResult
from .jenkins import BuildStatus
from .utils import log_error, log_info, log_success, log_warn

if TYPE_CHECKING:
    from .config_types import Config, Job
    from .history import BuildRecord
    from .jenkins import JenkinsClient

# 编号探测的并发上限，避免 Job 数量很多时占满线程
PROBE_WORKERS = 8


@dataclass(frozen=True)
class Selection:
    """一次构建选择的结果

    Attributes:
        jobs: 合并分支覆写与参数覆写后的 Job 列表
        skipped_params: JSON 参数中无法作为 Jenkins 参数传递、已被跳过的键名
    """

    jobs: list[Job] = field(default_factory=list)
    skipped_params: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RebuildSelection:
    """从历史记录还原出的重建选择

    Attributes:
        jobs: 能在当前配置中找到的 Job 列表
        records: 与 jobs 一一对应的历史记录（调用方渲染重建编号用）
        missing: 在当前配置中已不存在的历史记录
    """

    jobs: list[Job] = field(default_factory=list)
    records: list[BuildRecord] = field(default_factory=list)
    missing: list[BuildRecord] = field(default_factory=list)


@dataclass(frozen=True)
class BlockingOutcome:
    """阻塞构建的结果

    Attributes:
        results: 每个 Job 的构建结果
        history_error: 历史写入失败原因；为空表示写入成功或未要求写入
    """

    results: list[BuildResult] = field(default_factory=list)
    history_error: str = ""


@dataclass(frozen=True)
class QueuedBuild:
    """成功进入 Jenkins 队列的构建

    Attributes:
        job_key: Job 标识
        queue_url: Jenkins 队列项 URL
        build_num: 构建编号；队列静默期未结束时为 None
    """

    job_key: str
    queue_url: str
    build_num: int | None


@dataclass(frozen=True)
class TriggerFailure:
    """触发失败的 Job

    Attributes:
        job_key: Job 标识
        error: 失败原因
    """

    job_key: str
    error: str


@dataclass(frozen=True)
class QueueOutcome:
    """队列触发的结果

    Attributes:
        triggered: 已入队的构建，保持触发顺序
        failed: 触发失败的 Job，保持触发顺序
        history_error: 历史写入失败原因；为空表示写入成功或未要求写入
    """

    triggered: list[QueuedBuild] = field(default_factory=list)
    failed: list[TriggerFailure] = field(default_factory=list)
    history_error: str = ""


def parse_params(text: str) -> tuple[dict[str, Any], list[str]]:
    """解析调用方传入的参数字符串

    支持两种格式：
    1. JSON 对象: '{"BRANCH": "develop", "skip_tests": "true"}'
    2. URL 编码格式: 'BRANCH=develop&skip_tests=true'

    以 ``{`` 开头视为明确的 JSON 意图，解析失败直接抛错而不静默降级，
    避免用户指定的构建参数被悄悄丢弃。

    Args:
        text: 参数字符串

    Returns:
        (参数字典, 被跳过的键名列表) 元组；非标量值（None/列表/字典）
        无法作为 Jenkins 参数传递，其键名会出现在跳过列表中

    Raises:
        ValueError: 参数以 "{" 开头但不是合法的 JSON 对象

    Example:
        >>> parse_params('BRANCH=develop')
        ({'BRANCH': 'develop'}, [])
        >>> parse_params('{"skip": true}')
        ({'skip': 'true'}, [])
    """
    if not text or not text.strip():
        return {}, []

    text = text.strip()

    # 以 { 开头即按 JSON 解析，失败不再回退到 key=value
    if text.startswith("{"):
        try:
            result = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"参数不是合法的 JSON: {e}") from e
        if not isinstance(result, dict):
            raise ValueError("JSON 参数必须是对象（键值对）")

        # 仅保留可安全字符串化的标量（字符串/数字/布尔），
        # 跳过 None（避免转成 "None"）和列表/字典等非标量值；
        # 布尔值转小写 "true"/"false"，其余统一转为字符串。
        parsed: dict[str, Any] = {}
        skipped: list[str] = []
        for key, value in result.items():
            if value is not None and isinstance(value, (str, int, float, bool)):
                parsed[str(key)] = (
                    str(value).lower() if isinstance(value, bool) else str(value)
                )
            else:
                skipped.append(str(key))
        return parsed, skipped

    # key=value&key=value 格式复用配置层已有的解析实现，避免规则漂移
    from .config_io import _parse_params_field

    return _parse_params_field(text), []


def select_jobs(
    config: Config,
    *,
    env: str | None = None,
    filters: list[str] | None = None,
    branch: str = "",
    params_text: str = "",
) -> Selection:
    """选出本次要构建的 Job，并应用分支与参数覆写

    筛选规则由 Config.get_jobs 提供；本函数只组合调用方传入的覆写：
    先按环境解析 branch_field 写入自定义分支，再合并额外参数
    （参数覆写优先于分支覆写，与既有行为一致）。

    Args:
        config: Config 实例
        env: 按环境过滤
        filters: 按项目过滤，格式 ["env:project"] 或 ["project"]
        branch: 自定义分支，为空时不覆写
        params_text: 额外构建参数文本，支持 JSON 或 key=value&key=value

    Returns:
        Selection；没有匹配到 Job 时不解析参数，直接返回空选择

    Raises:
        ValueError: params_text 以 "{" 开头但不是合法的 JSON 对象

    Example:
        >>> select_jobs(config, env="dev", branch="hotfix")  # doctest: +SKIP
        Selection(jobs=[...], skipped_params=[])
    """
    jobs = config.get_jobs(env=env, jobs=filters)
    if not jobs:
        return Selection()

    if branch:
        for job in jobs:
            job.branch = branch
            job.params[config.branch_field_for(job.env)] = branch

    extra_params, skipped_params = parse_params(params_text)
    if extra_params:
        for job in jobs:
            job.params.update(extra_params)

    return Selection(jobs=jobs, skipped_params=skipped_params)


def select_rebuild_jobs(
    config: Config, records: list[BuildRecord]
) -> RebuildSelection:
    """从历史记录还原可重建的 Job

    Args:
        config: 当前配置
        records: 上次构建分组的历史记录

    Returns:
        RebuildSelection；配置中已不存在的项目进入 missing，供调用方提示

    Example:
        >>> select_rebuild_jobs(config, records)  # doctest: +SKIP
        RebuildSelection(jobs=[...], records=[...], missing=[])
    """
    jobs: list[Job] = []
    paired: list[BuildRecord] = []
    missing: list[BuildRecord] = []

    for record in records:
        job = config.create_job_from_record(record)
        if job is None:
            missing.append(record)
            continue
        jobs.append(job)
        paired.append(record)

    return RebuildSelection(jobs=jobs, records=paired, missing=missing)


def run_blocking(
    client: JenkinsClient,
    config: Config,
    jobs: list[Job],
    log_dir: str,
    *,
    mode: str = "parallel",
    history_file: str = "",
) -> BlockingOutcome:
    """阻塞构建：等待每个 Job 完成后返回结果，并写入历史记录

    同一批构建共用一个时间戳，保持 get_last_build_group 的分组语义；
    失败记录同样入库，便于历史统计。

    Args:
        client: Jenkins 客户端（调用方负责生命周期与连接预检）
        config: 配置对象（超时、轮询间隔、并行上限）
        jobs: 要构建的 Job 列表
        log_dir: 日志保存目录
        mode: "parallel" 时并行执行，其余值按顺序执行
        history_file: 历史文件路径，为空时跳过写入

    Returns:
        BlockingOutcome；历史写入失败不抛错，原因放在 history_error

    Example:
        >>> outcome = run_blocking(client, config, jobs, "logs")  # doctest: +SKIP
        >>> [r.status for r in outcome.results]
        [<BuildStatus.SUCCESS: 'SUCCESS'>]
    """
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    results: list[BuildResult] = []
    if jobs and mode == "parallel":
        with ThreadPoolExecutor(
            max_workers=min(len(jobs), max(1, config.build.max_parallel))
        ) as executor:
            future_to_job = {
                executor.submit(
                    _build_single, client, config, job, log_dir
                ): job
                for job in jobs
            }
            for future in as_completed(future_to_job):
                results.append(future.result())
    else:
        for job in jobs:
            results.append(_build_single(client, config, job, log_dir))

    history_error = ""
    if history_file:
        from .history import HistoryManager

        records = _records_from_results(
            jobs, results, datetime.now().isoformat(timespec="seconds")
        )
        try:
            HistoryManager(history_file).add_batch(records)
        except Exception as e:
            # 历史落盘失败（磁盘满、目录只读等）不应吞掉已完成的构建结果，
            # 由调用方决定如何提示
            history_error = str(e)

    return BlockingOutcome(results=results, history_error=history_error)


def trigger_queued(
    client: JenkinsClient,
    jobs: list[Job],
    *,
    history_file: str = "",
    probe_timeout: int = 10,
    branch_field_for: Callable[[str], str] | None = None,
    wait_build_num: bool = True,
) -> QueueOutcome:
    """触发一组构建后立即返回，不等待构建完成

    成功入队的构建会以 BUILDING 占位记录写入历史；编号探测只占用
    probe_timeout 的整体预算，探测不到时记录 build_num=0。

    Args:
        client: Jenkins 客户端（调用方负责生命周期）
        jobs: 要触发的 Job 列表
        history_file: 历史文件路径，为空时跳过写入
        probe_timeout: 编号探测的整批墙钟预算（秒）
        branch_field_for: 按环境名解析分支参数名的可调用对象；
            为 None 时统一用 "branch"
        wait_build_num: 为 False 时跳过编号探测，仅返回 queue_url

    Returns:
        QueueOutcome；单个 Job 触发失败不抛错，历史写入失败原因放在
        history_error

    Example:
        >>> outcome = trigger_queued(client, jobs)  # doctest: +SKIP
        >>> outcome.triggered[0].build_num
        123
    """
    triggered: list[QueuedBuild] = []
    failed: list[TriggerFailure] = []
    queued: list[tuple[Job, str]] = []

    for job in jobs:
        try:
            queue_url, diagnostic = client.trigger_build(job.path, job.params)
            if not queue_url:
                failed.append(TriggerFailure(
                    job_key=job.key,
                    error=f"触发失败: {diagnostic or 'Jenkins 未返回队列 URL'}",
                ))
                continue
            queued.append((job, queue_url))
        except Exception as e:
            failed.append(TriggerFailure(job_key=job.key, error=f"触发异常: {e}"))

    if wait_build_num:
        build_nums = _probe_build_numbers(client, queued, probe_timeout)
    else:
        build_nums = [None] * len(queued)

    recorded: list[tuple[Job, int | None]] = []
    for (job, queue_url), build_num in zip(queued, build_nums):
        triggered.append(QueuedBuild(
            job_key=job.key,
            queue_url=queue_url,
            build_num=build_num,
        ))
        recorded.append((job, build_num))

    history_error = ""
    if history_file and recorded:
        try:
            _record_queued(recorded, history_file, branch_field_for)
        except Exception as e:
            history_error = str(e)

    return QueueOutcome(
        triggered=triggered,
        failed=failed,
        history_error=history_error,
    )


def _build_single(
    client: JenkinsClient, config: Config, job: Job, log_dir: str
) -> BuildResult:
    """执行单个构建的完整流程

    1. 触发构建 → 2. 获取构建编号 → 3. 等待完成 → 4. 保存日志
    """
    log_info(f"正在触发构建：{job.key} ({job.path})")

    queue_url, trigger_diagnostic = client.trigger_build(job.path, job.params)

    if not queue_url:
        log_error(f"触发构建失败：{job.key}")
        error_log_file = save_error_log(
            log_dir,
            job,
            error_type="trigger_failed",
            error_msg="触发构建失败 - Jenkins POST 请求未返回队列 URL",
            base_url=client.base_url,
            extra_info=trigger_diagnostic,
        )
        return BuildResult(
            job_key=job.key,
            build_num=0,
            status=BuildStatus.FAILURE,
            duration=0,
            log_file=error_log_file,
            error="触发构建失败",
            branch=job.branch,
            params=job.params,
            project_name=job.project_name,
        )

    log_info(f"构建已排队，等待分配编号：{job.key}")

    queue_timeout = config.build.queue_timeout
    build_num = client.get_build_number(queue_url, timeout=queue_timeout)

    if not build_num:
        log_error(f"获取构建编号超时：{job.key}")
        error_log_file = save_error_log(
            log_dir,
            job,
            error_type="queue_timeout",
            error_msg=f"获取构建编号超时 - {queue_timeout}秒内未分配执行器",
            extra_info=f"队列URL: {queue_url}",
        )
        return BuildResult(
            job_key=job.key,
            build_num=0,
            status=BuildStatus.TIMEOUT,
            duration=0,
            log_file=error_log_file,
            error="获取构建编号超时",
            branch=job.branch,
            params=job.params,
            project_name=job.project_name,
        )

    log_success(f"构建已触发，编号：#{build_num}")

    status = _wait_for_build(client, config, job, build_num)

    log_file = str(Path(log_dir) / f"{job.key}_#{build_num}.log")
    log_content = client.get_build_log(job.path, build_num)

    if not log_content and status != BuildStatus.SUCCESS:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        diagnostic_info = [
            "=" * 60,
            "构建日志获取失败或为空",
            "=" * 60,
            f"时间: {timestamp}",
            f"构建编号: #{build_num}",
            f"最终状态: {status.value}",
            "",
            "可能原因:",
            "  1. 构建刚刚开始，日志还未写入",
            "  2. Jenkins API 请求失败",
            "  3. 构建被中止或取消",
            "  4. 网络连接问题",
            "",
            "建议操作:",
            "  1. 检查 Jenkins 控制台查看构建状态",
            f"  2. 直接访问: {client.base_url}/job/{job.path}/{build_num}/console",
            "  3. 稍后重试获取日志",
            "=" * 60,
        ]
        log_content = "\n".join(diagnostic_info)
        log_warn("日志内容为空，已添加诊断信息")

    with open(log_file, "w", encoding="utf-8") as f:
        f.write(log_content)

    log_info(f"日志已保存：{log_file}")

    duration = client.get_build_status(job.path, build_num).duration

    if status == BuildStatus.SUCCESS:
        log_success(f"构建完成：{job.key} (#{build_num}) - 成功")
    elif status == BuildStatus.FAILURE:
        log_error(f"构建失败：{job.key} (#{build_num}) - 失败")
        error_lines = extract_error_lines(log_content)
        if error_lines:
            print()
            log_error("控制台错误信息:")
            for line in error_lines:
                print(f"    {line}")
            print()
    else:
        log_warn(f"构建中止：{job.key} (#{build_num})")

    return BuildResult(
        job_key=job.key,
        build_num=build_num,
        status=status,
        duration=duration,
        log_file=log_file,
        branch=job.branch,
        params=job.params,
        project_name=job.project_name,
    )


def _wait_for_build(
    client: JenkinsClient, config: Config, job: Job, build_num: int
) -> BuildStatus:
    """轮询构建状态，直到完成或超时"""
    start = time.time()
    timeout = config.build.build_timeout
    poll_interval = config.build.poll_interval
    unknown_count = 0

    while True:
        elapsed = time.time() - start
        if elapsed >= timeout:
            return BuildStatus.TIMEOUT

        info = client.get_build_status(job.path, build_num)

        if info.status in (
            BuildStatus.SUCCESS,
            BuildStatus.FAILURE,
            BuildStatus.ABORTED,
        ):
            return info.status

        if info.status == BuildStatus.UNKNOWN:
            unknown_count += 1
            if unknown_count >= 3:
                log_error(
                    f"连续 {unknown_count} 次状态查询失败: "
                    f"{job.key} (#{build_num})"
                )
                return BuildStatus.FAILURE
            log_warn(
                f"状态查询失败 ({unknown_count}/3): "
                f"{job.key} (#{build_num})"
            )
        else:
            unknown_count = 0

        mins, secs = divmod(int(elapsed), 60)
        log_info(f"监控中：{job.key} (#{build_num}) - 已运行 {mins}分{secs}秒")

        time.sleep(poll_interval)


def _records_from_results(
    jobs: list[Job], results: list[BuildResult], timestamp: str
) -> list[BuildRecord]:
    """把构建结果装配为历史记录（同一批共用 timestamp）"""
    from .history import BuildRecord

    jobs_by_key = {job.key: job for job in jobs}

    records: list[BuildRecord] = []
    for result in results:
        job = jobs_by_key.get(result.job_key)
        records.append(
            BuildRecord(
                timestamp=timestamp,
                env=job.env if job else "",
                job_key=result.job_key,
                build_num=result.build_num,
                status=result.status.value,
                duration=result.duration,
                log_file=result.log_file,
                branch=result.branch,
                params=result.params,
                project_name=result.project_name,
                job_path=job.path if job else "",
            )
        )
    return records


def _safe_build_number(
    client: JenkinsClient, queue_url: str, deadline: float
) -> int | None:
    """探测单个队列项的构建编号，异常时返回 None"""
    # 向上取整：首个探测应拿到完整预算，不因函数调用间的微秒开销被截断掉 1 秒
    remaining = math.ceil(deadline - time.monotonic())
    if remaining <= 0:
        return None
    try:
        return client.get_build_number(queue_url, timeout=remaining)
    except Exception:
        return None


def _probe_build_numbers(
    client: JenkinsClient, queued: list[tuple[Job, str]], budget: int
) -> list[int | None]:
    """并发探测各队列项的构建编号

    并发而非串行探测，使总耗时约等于单次探测预算，而不随 Job 数量
    线性增长。整批共享同一个截止时间，超出并发上限时后续批次按剩余
    时间收缩超时。

    Returns:
        与 queued 顺序一致的构建编号列表，未取到的位置为 None
    """
    if not queued:
        return []

    deadline = time.monotonic() + budget
    workers = min(PROBE_WORKERS, len(queued))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_safe_build_number, client, queue_url, deadline)
            for _, queue_url in queued
        ]
        return [future.result() for future in futures]


def _record_queued(
    triggered: list[tuple[Job, int | None]],
    history_file: str,
    branch_field_for: Callable[[str], str] | None,
) -> None:
    """把成功入队的构建写成 BUILDING 占位记录

    同一次触发的记录使用相同时间戳，保持 get_last_build_group 的分组语义。
    """
    from .config import env_from_job_key
    from .history import BuildRecord, HistoryManager

    timestamp = datetime.now().isoformat(timespec="seconds")
    records: list[BuildRecord] = []
    for job, build_num in triggered:
        job_key = getattr(job, "key", "") or ""
        env = getattr(job, "env", "") or ""
        if not env:
            env = env_from_job_key(job_key)
        params = dict(getattr(job, "params", None) or {})
        branch_field = branch_field_for(env) if branch_field_for else "branch"
        records.append(BuildRecord(
            timestamp=timestamp,
            env=env,
            job_key=job_key,
            build_num=build_num or 0,
            status=BuildStatus.BUILDING.value,
            duration=0,
            log_file="",
            branch=str(params.get(branch_field or "branch", "")),
            params=params,
            project_name=getattr(job, "project_name", "") or "",
            job_path=getattr(job, "path", "") or "",
        ))
    HistoryManager(history_file).add_batch(records)
