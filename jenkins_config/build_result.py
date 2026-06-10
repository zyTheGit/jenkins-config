# jenkins_config/build_result.py
"""
构建结果数据类型定义
"""

from dataclasses import dataclass, field
from typing import Optional

from .jenkins import BuildStatus


@dataclass
class BuildResult:
    """
    构建结果

    存储单次构建的完整结果信息。

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
