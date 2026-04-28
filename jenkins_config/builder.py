# jenkins_config/builder.py
"""
构建编排模块 - 管理构建流程的执行

这个模块负责协调整个构建流程：
1. 调用 Jenkins 客户端触发构建
2. 监控构建状态直到完成
3. 收集和保存构建日志
4. 支持并行和顺序两种构建模式

核心流程：
触发构建 → 获取构建编号 → 监控构建状态 → 保存日志 → 返回结果
"""

from __future__ import annotations
import time
from datetime import datetime
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from .jenkins import BuildStatus
from .utils import log_info, log_success, log_error, log_warn, log_debug

# TYPE_CHECKING 用于类型注解，避免循环导入
# 在运行时这些导入不会执行，只用于类型检查
if TYPE_CHECKING:
    from .jenkins import JenkinsClient
    from .config import Config, Job


# ============================================================================
# 数据类定义
# ============================================================================


@dataclass
class BuildResult:
    """
    构建结果

    存储单次构建的完整结果信息

    Attributes:
        job_key: Job 唯一标识
        build_num: 构建编号（触发失败时为 0）
        status: 构建状态
        duration: 构建耗时（秒）
        log_file: 日志文件路径
        error: 错误信息（成功时为 None）
        branch: 构建时使用的分支（用于记录到历史）
        params: 构建参数字典（用于记录到历史）
        project_name: 原始项目名称（用于记录到历史）
    """

    job_key: str
    build_num: int
    status: BuildStatus
    duration: int  # 秒
    log_file: str
    error: str | None = None
    branch: str = ""
    params: dict = field(default_factory=dict)
    project_name: str = ""


# ============================================================================
# 构建器类
# ============================================================================


class Builder:
    """
    构建编排器

    负责协调整个构建流程，是 CLI 和 Jenkins 客户端之间的桥梁。

    主要功能：
    1. 顺序构建：一个接一个执行
    2. 并行构建：使用线程池同时执行多个构建
    3. 单个构建流程：触发 → 监控 → 收集日志

    Attributes:
        client: Jenkins 客户端实例
        config: 配置对象

    Example:
        >>> client = JenkinsClient(url, token)
        >>> builder = Builder(client, config)
        >>> results = builder.build_parallel(jobs, log_dir)
    """

    def __init__(self, client: JenkinsClient, config: Config):
        """
        初始化构建器

        Args:
            client: Jenkins 客户端
            config: 配置对象（包含超时、轮询间隔等设置）
        """
        self.client = client
        self.config = config

    # ========================================================================
    # 公共方法：构建执行
    # ========================================================================

    def build_sequential(self, jobs: list[Job], log_dir: str) -> list[BuildResult]:
        """
        顺序构建

        按顺序逐个执行构建，一个完成后才执行下一个。
        适合有依赖关系的项目，或避免并发压力。

        Args:
            jobs: 要构建的 Job 列表
            log_dir: 日志保存目录

        Returns:
            BuildResult 列表（与 jobs 顺序一致）

        Example:
            >>> results = builder.build_sequential([job1, job2], "./logs")
            >>> for r in results:
            ...     print(f"{r.job_key}: {r.status}")
        """
        results = []
        for job in jobs:
            # 逐个执行，等待完成后再执行下一个
            result = self._build_single(job, log_dir)
            results.append(result)
        return results

    def build_parallel(self, jobs: list[Job], log_dir: str) -> list[BuildResult]:
        """
        并行构建

        使用线程池同时执行多个构建，显著减少总耗时。
        适合独立的、无依赖的项目。

        Args:
            jobs: 要构建的 Job 列表
            log_dir: 日志保存目录

        Returns:
            BuildResult 列表（顺序可能与 jobs 不同，按完成时间排序）

        Note:
            - 使用 concurrent.futures.ThreadPoolExecutor
            - max_workers 设置为 job 数量，不限制并发
            - as_completed 按完成顺序获取结果

        Example:
            >>> results = builder.build_parallel([job1, job2, job3], "./logs")
            >>> print(f"已完成 {len(results)} 个构建")
        """
        results = []

        # 创建线程池，线程数等于 job 数量
        # 这样每个构建都可以同时开始
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            # 提交所有任务，记录 future 到 job 的映射
            future_to_job = {
                executor.submit(self._build_single, job, log_dir): job for job in jobs
            }

            # as_completed 返回已完成的 future（按完成顺序）
            # 这样可以尽早处理完成的构建
            for future in as_completed(future_to_job):
                result = future.result()  # 获取结果（会阻塞直到完成）
                results.append(result)

        return results

    # ========================================================================
    # 私有方法：单个构建流程
    # ========================================================================

    def _build_single(self, job: Job, log_dir: str) -> BuildResult:
        """
        执行单个构建的完整流程

        这是构建的核心逻辑，包含以下步骤：
        1. 触发构建
        2. 获取构建编号
        3. 等待构建完成
        4. 保存构建日志

        Args:
            job: 要构建的 Job
            log_dir: 日志保存目录

        Returns:
            BuildResult 对象
        """
        log_info(f"正在触发构建：{job.key} ({job.path})")

        Path(log_dir).mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------
        # 步骤 1：触发构建
        # ------------------------------------------------------------
        queue_url = self.client.trigger_build(job.path, job.params)

        if not queue_url:
            log_error(f"触发构建失败：{job.key}")
            error_log_file = self._save_error_log(
                log_dir,
                job,
                error_type="trigger_failed",
                error_msg="触发构建失败 - Jenkins POST 请求未返回队列 URL",
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

        # ------------------------------------------------------------
        # 步骤 2：获取构建编号
        # Jenkins 会先将构建放入队列，然后分配执行器和编号
        # ------------------------------------------------------------
        build_num = self.client.get_build_number(queue_url, timeout=30)

        if not build_num:
            log_error(f"获取构建编号超时：{job.key}")
            error_log_file = self._save_error_log(
                log_dir,
                job,
                error_type="queue_timeout",
                error_msg="获取构建编号超时 - 30秒内未分配执行器",
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

        # ------------------------------------------------------------
        # 步骤 3：等待构建完成
        # ------------------------------------------------------------
        status = self._wait_for_build(job, build_num)

        # ------------------------------------------------------------
        # 步骤 4：获取并保存日志
        # ------------------------------------------------------------

        # 日志文件命名：{job_key}_#{build_num}.log
        log_file = f"{log_dir}/{job.key}_#{build_num}.log"

        # 获取日志内容
        log_content = self.client.get_build_log(job.path, build_num)

        # 如果日志为空且状态不是成功，添加诊断信息
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
                f"  2. 直接访问: {self.client.base_url}/job/{job.path}/{build_num}/console",
                "  3. 稍后重试获取日志",
                "=" * 60,
            ]
            log_content = "\n".join(diagnostic_info)
            log_warn(f"日志内容为空，已添加诊断信息")

        # 写入文件
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(log_content)

        log_info(f"日志已保存：{log_file}")

        # 获取构建耗时
        duration = self.client.get_build_status(job.path, build_num).duration

        # ------------------------------------------------------------
        # 步骤 5：输出结果
        # ------------------------------------------------------------
        if status == BuildStatus.SUCCESS:
            log_success(f"构建完成：{job.key} (#{build_num}) - 成功")
        elif status == BuildStatus.FAILURE:
            log_error(f"构建失败：{job.key} (#{build_num}) - 失败")
            # 提取并显示错误信息
            error_lines = self._extract_error_lines(log_content)
            if error_lines:
                print()
                log_error("控制台错误信息:")
                for line in error_lines:
                    print(f"    {line}")
                print()
        else:
            log_warn(f"构建中止：{job.key} (#{build_num})")

        # 返回结果
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

    # ========================================================================
    # 私有方法：错误信息提取
    # ========================================================================

    def _extract_error_lines(self, log_content: str, max_lines: int = 50) -> list[str]:
        """
        从日志中提取错误行

        搜索包含错误关键词的行，用于在控制台显示关键错误信息

        Args:
            log_content: 构建日志内容
            max_lines: 最多返回的行数

        Returns:
            包含错误信息的行列表
        """
        if not log_content:
            return []

        error_keywords = [
            "ERROR",
            "FAILURE",
            "Failed",
            "Exception",
            "error:",
            "fatal:",
            "FATAL",
            "BUILD FAILED",
            "Execution failed",
            "CMake Error",
            "make:",
            "Makefile",
            "npm ERR!",
            "ERR!",
            "Traceback",
            "called from",
            "AssertionError",
            "KeyError",
            "TypeError",
            "ValueError",
            "NameError",
            "ImportError",
            "ModuleNotFoundError",
            "FileNotFoundError",
            "PermissionError",
            "ConnectionError",
            "HTTPConnectionPool",
            "Connection refused",
            "timeout",
            "TimeoutError",
            "SSL",
            "certificate",
            "auth",
            "Unauthorized",
            "Forbidden",
            "401",
            "403",
            "404",
            "500",
        ]

        lines = log_content.split("\n")
        error_lines = []

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            for keyword in error_keywords:
                if keyword.lower() in line_stripped.lower():
                    error_lines.append(line_stripped)
                    break

            if len(error_lines) >= max_lines:
                break

        if not error_lines:
            last_lines = [l.strip() for l in lines[-30:] if l.strip()]
            return last_lines[-max_lines:] if last_lines else []

        return error_lines

    def _save_error_log(
        self,
        log_dir: str,
        job: Job,
        error_type: str,
        error_msg: str,
        extra_info: str = "",
    ) -> str:
        """
        保存错误日志文件

        在触发失败或获取编号超时时，创建错误日志文件记录失败信息。

        Args:
            log_dir: 日志目录
            job: Job 对象
            error_type: 错误类型 (trigger_failed / queue_timeout)
            error_msg: 错误消息
            extra_info: 额外信息

        Returns:
            错误日志文件路径
        """
        error_log_file = f"{log_dir}/{job.key}_error.log"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content_lines = [
            "=" * 60,
            f"构建错误日志 - {job.key}",
            "=" * 60,
            f"时间: {timestamp}",
            f"Job 路径: {job.path}",
            f"项目名称: {job.project_name}",
            f"分支: {job.branch}",
            "",
            f"错误类型: {error_type}",
            f"错误详情: {error_msg}",
        ]

        if extra_info:
            content_lines.append(f"附加信息: {extra_info}")

        if job.params:
            content_lines.append("")
            content_lines.append("构建参数:")
            for k, v in job.params.items():
                content_lines.append(f"  {k}: {v}")

        content_lines.append("")
        content_lines.append("=" * 60)
        content_lines.append("诊断建议:")
        content_lines.append("")
        if error_type == "trigger_failed":
            content_lines.extend(
                [
                    "  1. 检查 Jenkins Job 是否存在",
                    "  2. 检查用户是否有触发该 Job 的权限",
                    "  3. 检查 Jenkins 服务器是否正常运行",
                    "  4. 检查网络连接是否正常",
                ]
            )
        elif error_type == "queue_timeout":
            content_lines.extend(
                [
                    "  1. 检查 Jenkins 执行器是否繁忙",
                    "  2. 检查队列中是否有其他构建阻塞",
                    "  3. 考虑增加超时时间配置",
                    "  4. 检查构建是否被手动取消",
                ]
            )
        content_lines.append("=" * 60)

        with open(error_log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(content_lines))

        log_info(f"错误日志已保存：{error_log_file}")
        return error_log_file

    # ========================================================================
    # 私有方法：构建监控
    # ========================================================================

    def _wait_for_build(self, job: Job, build_num: int) -> BuildStatus:
        """
        等待构建完成

        轮询 Jenkins API 检查构建状态，直到构建完成或超时。

        Args:
            job: Job 对象
            build_num: 构建编号

        Returns:
            最终的构建状态

        Note:
            - 轮询间隔由 config.build.poll_interval 控制
            - 超时时间由 config.build.build_timeout 控制
        """
        start = time.time()
        timeout = self.config.build.build_timeout
        poll_interval = self.config.build.poll_interval

        log_debug(
            f"开始监控构建 #{build_num}，超时: {timeout}秒，轮询间隔: {poll_interval}秒"
        )

        while True:
            # 检查是否超时
            elapsed = time.time() - start
            if elapsed >= timeout:
                log_debug(f"构建监控超时: {elapsed}秒 >= {timeout}秒")
                return BuildStatus.TIMEOUT

            # 查询当前状态
            info = self.client.get_build_status(job.path, build_num)

            log_debug(f"构建 #{build_num} 当前状态: {info.status.value}")

            # 检查是否完成（SUCCESS、FAILURE、ABORTED 都是终态）
            if info.status in (
                BuildStatus.SUCCESS,
                BuildStatus.FAILURE,
                BuildStatus.ABORTED,
            ):
                log_debug(f"构建 #{build_num} 已完成: {info.status.value}")
                return info.status

            # 输出进度信息
            mins, secs = divmod(int(elapsed), 60)
            log_info(f"监控中：{job.key} (#{build_num}) - 已运行 {mins}分{secs}秒")

            # 等待下一次轮询
            time.sleep(poll_interval)
