# jenkins_config/build_errors.py
"""
构建错误处理模块 - 错误日志生成和错误信息提取
"""

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Job


def save_error_log(
    log_dir: str,
    job: Job,
    error_type: str,
    error_msg: str,
    base_url: str = "",
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
        base_url: Jenkins 基础 URL（用于诊断信息）
        extra_info: 额外信息

    Returns:
        错误日志文件路径
    """
    error_log_file = str(Path(log_dir) / f"{job.key}_error.log")

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
        content_lines.extend([
            "  1. 检查 Jenkins Job 是否存在",
            "  2. 检查用户是否有触发该 Job 的权限",
            "  3. 检查 Jenkins 服务器是否正常运行",
            "  4. 检查网络连接是否正常",
        ])
    elif error_type == "queue_timeout":
        content_lines.extend([
            "  1. 检查 Jenkins 执行器是否繁忙",
            "  2. 检查队列中是否有其他构建阻塞",
            "  3. 考虑增加超时时间配置",
            "  4. 检查构建是否被手动取消",
        ])

    if base_url and job.path:
        content_lines.extend([
            "",
            "Jenkins 链接:",
            f"  {base_url}/job/{job.path}/",
        ])

    content_lines.append("=" * 60)

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    with open(error_log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(content_lines))

    return error_log_file


def extract_error_lines(log_content: str | None, max_lines: int = 50) -> list[str]:
    """
    从日志中提取错误行

    搜索包含错误关键词的行，用于在控制台显示关键错误信息。

    Args:
        log_content: 构建日志内容
        max_lines: 最多返回的行数

    Returns:
        包含错误信息的行列表
    """
    if not log_content:
        return []

    error_keywords = [
        "ERROR", "FAILURE", "Failed", "Exception", "error:", "fatal:",
        "FATAL", "BUILD FAILED", "Execution failed", "CMake Error",
        "make:", "Makefile", "npm ERR!", "ERR!", "Traceback",
        "called from", "AssertionError", "KeyError", "TypeError",
        "ValueError", "NameError", "ImportError", "ModuleNotFoundError",
        "FileNotFoundError", "PermissionError", "ConnectionError",
        "HTTPConnectionPool", "Connection refused", "timeout",
        "TimeoutError", "SSL", "certificate", "auth", "Unauthorized",
        "Forbidden", "401", "403", "404", "500",
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
