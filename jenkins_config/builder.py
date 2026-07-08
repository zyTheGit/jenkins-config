# jenkins_config/builder.py
"""
构建编排模块 - 管理构建流程的执行

负责协调整个构建流程：触发构建 → 获取构建编号 → 监控状态 → 保存日志。
"""

from __future__ import annotations
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from .jenkins import BuildStatus
from .build_result import BuildResult
from .build_errors import save_error_log, extract_error_lines
from .utils import log_info, log_success, log_error, log_warn, log_debug

if TYPE_CHECKING:
    from .jenkins import JenkinsClient
    from .config import Config, Job


class Builder:
    """
    构建编排器

    负责协调整个构建流程，是 CLI 和 Jenkins 客户端之间的桥梁。

    Attributes:
        client: Jenkins 客户端实例
        config: 配置对象
    """

    def __init__(self, client: JenkinsClient, config: Config):
        """
        初始化构建器

        Args:
            client: Jenkins 客户端
            config: 配置对象
        """
        self.client = client
        self.config = config

    # ========================================================================
    # 公共方法：构建执行
    # ========================================================================

    def build_sequential(self, jobs: list[Job], log_dir: str) -> list[BuildResult]:
        """
        顺序构建 - 按顺序逐个执行构建

        Args:
            jobs: 要构建的 Job 列表
            log_dir: 日志保存目录

        Returns:
            BuildResult 列表（与 jobs 顺序一致）
        """
        results = []
        for job in jobs:
            result = self._build_single(job, log_dir)
            results.append(result)
        return results

    def build_parallel(self, jobs: list[Job], log_dir: str) -> list[BuildResult]:
        """
        并行构建 - 使用线程池同时执行多个构建

        Args:
            jobs: 要构建的 Job 列表
            log_dir: 日志保存目录

        Returns:
            BuildResult 列表（按完成时间排序）
        """
        results = []

        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            future_to_job = {
                executor.submit(self._build_single, job, log_dir): job
                for job in jobs
            }

            for future in as_completed(future_to_job):
                result = future.result()
                results.append(result)

        return results

    # ========================================================================
    # 私有方法：单个构建流程
    # ========================================================================

    def _build_single(self, job: Job, log_dir: str) -> BuildResult:
        """
        执行单个构建的完整流程

        1. 触发构建 → 2. 获取构建编号 → 3. 等待完成 → 4. 保存日志
        """
        log_info(f"正在触发构建：{job.key} ({job.path})")

        Path(log_dir).mkdir(parents=True, exist_ok=True)

        # 步骤 1：触发构建
        queue_url, trigger_diagnostic = self.client.trigger_build(job.path, job.params)

        if not queue_url:
            log_error(f"触发构建失败：{job.key}")
            error_log_file = save_error_log(
                log_dir,
                job,
                error_type="trigger_failed",
                error_msg="触发构建失败 - Jenkins POST 请求未返回队列 URL",
                base_url=self.client.base_url,
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

        # 步骤 2：获取构建编号
        build_num = self.client.get_build_number(queue_url, timeout=30)

        if not build_num:
            log_error(f"获取构建编号超时：{job.key}")
            error_log_file = save_error_log(
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

        # 步骤 3：等待构建完成
        status = self._wait_for_build(job, build_num)

        # 步骤 4：获取并保存日志
        log_file = str(Path(log_dir) / f"{job.key}_#{build_num}.log")

        log_content = self.client.get_build_log(job.path, build_num)

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

        with open(log_file, "w", encoding="utf-8") as f:
            f.write(log_content)

        log_info(f"日志已保存：{log_file}")

        duration = self.client.get_build_status(job.path, build_num).duration

        # 输出结果
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
        """
        start = time.time()
        timeout = self.config.build.build_timeout
        poll_interval = self.config.build.poll_interval

        while True:
            elapsed = time.time() - start
            if elapsed >= timeout:
                return BuildStatus.TIMEOUT

            info = self.client.get_build_status(job.path, build_num)

            if info.status in (
                BuildStatus.SUCCESS,
                BuildStatus.FAILURE,
                BuildStatus.ABORTED,
            ):
                return info.status

            mins, secs = divmod(int(elapsed), 60)
            log_info(f"监控中：{job.key} (#{build_num}) - 已运行 {mins}分{secs}秒")

            time.sleep(poll_interval)
