# jenkins_config/cmd_build.py
"""
构建执行命令模块

包含构建流程、重建、报告生成和日志清理功能。
"""

import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .config_io import _parse_params_field
from .jenkins import JenkinsClient, BuildStatus
from .builder import Builder
from .build_result import BuildResult
from .history import HistoryManager, BuildRecord
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
    jobs = config.get_jobs(env=args.env, jobs=jobs_filter)

    if not jobs:
        log_error("没有找到匹配的项目")
        sys.exit(1)

    # 覆盖分支参数
    if getattr(args, "branch", None):
        custom_branch = args.branch
        branch_field = config.branch_field
        log_info(f"使用自定义分支: {custom_branch} (参数名: {branch_field})")
        for job in jobs:
            job.branch = custom_branch
            job.params[branch_field] = custom_branch

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

    _cleanup_old_logs(config.build.log_dir, config.build.log_retention_days)

    log_dir = Path(config.build.log_dir) / f"build_{datetime.now().strftime('%Y%m%d')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"日志目录: {log_dir}")

    # 覆盖参数 -p
    if args.params:
        override_params = _parse_params_field(args.params)
        for job in jobs:
            job.params.update(override_params)

    client = JenkinsClient(
        url=config.server.url,
        username=config.server.username,
        token=config.server.token,
        timeout=config.build.curl_timeout,
    )
    builder = Builder(client, config)

    if args.mode == "parallel":
        results = builder.build_parallel(jobs, str(log_dir))
    else:
        results = builder.build_sequential(jobs, str(log_dir))

    # 保存历史记录
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))

    for result in results:
        job = next((j for j in jobs if j.key == result.job_key), None)
        manager.add(
            BuildRecord(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                env=job.env if job else "",
                job_key=result.job_key,
                build_num=result.build_num,
                status=result.status.value,
                duration=result.duration,
                log_file=result.log_file,
                branch=result.branch,
                params=result.params,
                project_name=result.project_name,
            )
        )

    generate_report(results, str(log_dir))


def run_rebuild_last(config_file: Path, args):
    """重建上次构建的项目"""
    print_header("重建上次构建")

    config = Config.load(str(config_file))
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))

    last_group = manager.get_last_build_group()

    if not last_group:
        log_error("没有找到上次成功构建的记录")
        sys.exit(1)

    print_sep("-")
    print(f"上次构建时间: {last_group[0].timestamp}")
    print(f"将要重建的项目 (共 {len(last_group)} 个):")
    print_sep("-")

    jobs = []
    for record in last_group:
        job = config.create_job_from_record(record)
        if job:
            jobs.append(job)
            print(f"  - [{record.env}] {record.job_key} #{record.build_num}")
            if record.branch:
                print(f"      分支: {record.branch}")
        else:
            log_warn(f"项目 '{record.project_name}' 在配置中不存在，跳过")

    print_sep("-")
    print()

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

    _cleanup_old_logs(config.build.log_dir, config.build.log_retention_days)

    log_dir = Path(config.build.log_dir) / f"build_{datetime.now().strftime('%Y%m%d')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"日志目录: {log_dir}")

    client = JenkinsClient(
        url=config.server.url,
        username=config.server.username,
        token=config.server.token,
        timeout=config.build.curl_timeout,
    )
    builder = Builder(client, config)

    if args.mode == "parallel":
        results = builder.build_parallel(jobs, str(log_dir))
    else:
        results = builder.build_sequential(jobs, str(log_dir))

    for result in results:
        job = next((j for j in jobs if j.key == result.job_key), None)
        manager.add(
            BuildRecord(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                env=job.env if job else "",
                job_key=result.job_key,
                build_num=result.build_num,
                status=result.status.value,
                duration=result.duration,
                log_file=result.log_file,
                branch=result.branch,
                params=result.params,
                project_name=result.project_name,
            )
        )

    generate_report(results, str(log_dir))


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
    print_sep("=")

    if failure > 0:
        sys.exit(1)
