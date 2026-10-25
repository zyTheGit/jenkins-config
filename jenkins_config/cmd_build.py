# jenkins_config/cmd_build.py
"""
构建执行命令模块

包含构建流程、重建、报告生成和日志清理功能。
"""

import shutil
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from .build_flow import run_blocking, select_jobs, select_rebuild_jobs
from .config import Config
from .jenkins import JenkinsClient, BuildStatus
from .build_result import BuildResult
from .history import HistoryManager
from .paths import resolve_history_path

from .utils import (
    log_info,
    log_success,
    log_error,
    log_warn,
    print_sep,
    print_header,
)


def run_build(config_file: Path, args):
    """
    执行构建流程

    1. 加载配置
    2. 获取 jobs
    3. 创建 Jenkins 客户端和构建器
    4. 执行构建（并行或顺序）
    5. 保存历史记录
    6. 生成报告
    """
    print_header("Jenkins 自动构建脚本")
    print(f"构建模式: {args.mode}")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        config = Config.load(str(config_file))
    except FileNotFoundError as e:
        log_error(str(e))
        sys.exit(1)

    jobs_filter = args.jobs.split(",") if args.jobs else None

    try:
        selection = select_jobs(
            config,
            env=args.env,
            filters=jobs_filter,
            branch=getattr(args, "branch", "") or "",
            params_text=getattr(args, "params", "") or "",
        )
    except ValueError as e:
        log_error(f"参数解析失败: {e}")
        sys.exit(1)

    jobs = selection.jobs

    if not jobs:
        log_error("没有找到匹配的项目")
        sys.exit(1)

    # 覆盖分支参数
    if getattr(args, "branch", None):
        custom_branch = args.branch
        # 分支参数名按 job 所属环境解析（环境级 branch_field 优先于全局）
        for job in jobs:
            branch_field = config.branch_field_for(job.env)
            log_info(f"使用自定义分支: {custom_branch} (参数名: {branch_field})")

    if selection.skipped_params:
        log_warn(
            "以下参数无法作为 Jenkins 参数传递，已跳过: "
            + ", ".join(selection.skipped_params)
        )


    # 显示将要构建的项目
    print_sep("-")
    print(f"将要构建的 Job (共 {len(jobs)} 个):")
    print_sep("-")
    for job in jobs:
        print(f"  - [{job.env}] {job.key}: {job.path} (分支: {job.branch})")
    print_sep("-")
    print()

    if not getattr(args, "yes", False):
        try:
            print("是否确认开始构建? [Y/n]: ", end="", flush=True)
            response = input().strip().lower()
            if response not in ("", "y", "yes"):
                log_warn("已取消构建")
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            print("\n")
            log_warn("已取消构建")
            sys.exit(130)

    _execute_build(config, jobs, config_file, args)


def run_rebuild_last(config_file: Path, args):
    """重建上次构建的项目"""
    print_header("重建上次构建")

    config = Config.load(str(config_file))
    manager = HistoryManager(str(resolve_history_path(config_file)))


    last_group = manager.get_last_build_group()

    if not last_group:
        log_error("没有找到上次成功构建的记录")
        sys.exit(1)

    print_sep("-")
    print(f"上次构建时间: {last_group[0].timestamp}")
    print(f"将要重建的项目 (共 {len(last_group)} 个):")
    print_sep("-")

    selection = select_rebuild_jobs(config, last_group)

    for record, job in zip(selection.records, selection.jobs):
        print(f"  - [{record.env}] {job.key} #{record.build_num}")
        if record.branch:
            print(f"      分支: {record.branch}")

    for record in selection.missing:
        log_warn(f"项目 '{record.project_name}' 在配置中不存在，跳过")

    print_sep("-")
    print()

    jobs = selection.jobs

    if not jobs:
        log_error("没有可重建的项目")
        sys.exit(1)

    if not getattr(args, "yes", False):
        try:
            print("是否确认开始重建? [Y/n]: ", end="", flush=True)
            response = input().strip().lower()
            if response not in ("", "y", "yes"):
                log_warn("已取消重建")
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            print("\n")
            log_warn("已取消重建")
            sys.exit(130)

    _execute_build(config, jobs, config_file, args)


def _execute_build(config, jobs, config_file, args):
    """
    公共构建执行逻辑

    包含：日志清理、日志目录创建、客户端创建、构建执行、历史保存、报告生成。

    Args:
        config: Config 对象
        jobs: Job 列表
        config_file: 配置文件路径
        args: CLI 参数
    """
    _cleanup_old_logs(config.build.log_dir, config.build.log_retention_days)

    log_dir = Path(config.build.log_dir) / f"build_{datetime.now().strftime('%Y%m%d')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"日志目录: {log_dir}")

    # 客户端持有 requests.Session，用 closing 确保构建结束后释放连接池
    with closing(JenkinsClient.from_config(config)) as client:
        # 构建前检查 Jenkins 连接
        try:
            client.health_check()
        except ConnectionError as e:
            log_error(str(e))
            sys.exit(1)

        outcome = run_blocking(
            client,
            config,
            jobs,
            str(log_dir),
            mode=args.mode,
            history_file=str(resolve_history_path(config_file)),
        )

    # 历史落盘失败（磁盘满、目录只读等）不应吞掉已完成的构建报告
    if outcome.history_error:
        log_warn(
            f"构建历史保存失败（{outcome.history_error}），"
            "本次构建结果仅输出报告"
        )

    generate_report(outcome.results, str(log_dir))


def _cleanup_old_logs(log_dir: str, retention_days: int):
    """清理超过保留天数的旧日志目录"""
    log_root = Path(log_dir)
    if not log_root.exists():
        return

    cutoff = datetime.now() - timedelta(days=retention_days)
    cutoff_str = cutoff.strftime("%Y%m%d")
    cleaned = 0

    for dir_path in sorted(log_root.iterdir()):
        if not dir_path.is_dir():
            continue
        if not dir_path.name.startswith("build_"):
            continue
        date_part = dir_path.name[len("build_"):]
        if len(date_part) != 8 or not date_part.isdigit():
            continue
        if date_part < cutoff_str:
            shutil.rmtree(dir_path)
            log_info(f"已清理旧日志目录: {dir_path}")
            cleaned += 1

    if cleaned > 0:
        log_info(f"共清理 {cleaned} 个旧日志目录（保留 {retention_days} 天内的日志）")


def generate_report(results: list[BuildResult], log_dir: str):
    """生成构建结果报告"""
    print_header("构建结果汇总")

    total = len(results)
    success = sum(1 for r in results if r.status == BuildStatus.SUCCESS)
    failure = total - success

    print(f"总计: {total} 个 Job")
    print(f"成功: {success} 个")
    print(f"失败: {failure} 个")
    print()

    print_sep("-")
    print("详细结果:")
    print_sep("-")

    for result in results:
        if result.status == BuildStatus.SUCCESS:
            print(f"  [OK] {result.job_key}: SUCCESS (#{result.build_num})")
        elif result.status == BuildStatus.FAILURE:
            print(f"  [FAIL] {result.job_key}: FAILURE (#{result.build_num})")
        elif result.status == BuildStatus.ABORTED:
            print(f"  [ABORT] {result.job_key}: ABORTED (#{result.build_num})")
        elif result.status == BuildStatus.TIMEOUT:
            print(f"  [TIMEOUT] {result.job_key}: TIMEOUT (#{result.build_num})")
        else:
            print(f"  [?] {result.job_key}: {result.status.value} (#{result.build_num})")

    print_sep("-")
    print(f"日志目录: {log_dir}")
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print_sep("=")

    if failure > 0:
        sys.exit(1)
