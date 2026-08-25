# jenkins_config/history.py
"""
历史记录模块 - 管理构建历史的持久化存储

这个模块提供构建历史的存储和查询功能：
1. 记录每次构建的结果
2. 查询历史记录
3. 统计成功/失败率
4. 自动限制记录数量

数据存储在 JSON 文件中，格式如下：
{
    "records": [
        {
            "timestamp": "2026-03-20T10:00:00",
            "env": "dev",
            "job_key": "dev_project",
            "build_num": 123,
            "status": "SUCCESS",
            "duration": 60,
            "log_file": "/path/to/log"
        }
    ]
}
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jenkins_config.filelock import atomic_write, file_lock
from jenkins_config.utils import log_warn


# ============================================================================


# 数据类定义
# ============================================================================


@dataclass
class BuildRecord:
    """
    构建记录

    存储单次构建的完整记录，用于历史查询和统计。

    Attributes:
        timestamp: 时间戳（ISO 格式）
        env: 环境名称
        job_key: Job 唯一标识
        build_num: 构建编号
        status: 构建状态字符串（SUCCESS、FAILURE 等）
        duration: 构建耗时（秒）
        log_file: 日志文件路径
        branch: 构建时使用的分支（用于重建）
        params: 构建参数字典（用于重建）
        project_name: 原始项目名称（用于重建时查找 config）
        job_path: Jenkins Job 路径（可与 project_name 不同，用于无配置的直连重建）
    """

    timestamp: str
    env: str
    job_key: str
    build_num: int
    status: str
    duration: int
    log_file: str
    branch: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    project_name: str = ""
    job_path: str = ""


# ============================================================================
# 历史管理器类
# ============================================================================


class HistoryManager:
    """
    构建历史管理器

    负责构建历史的增删查改，数据持久化到 JSON 文件。

    主要功能：
    1. 添加构建记录
    2. 查询历史记录（支持按环境过滤）
    3. 统计成功/失败率
    4. 自动限制记录数量

    Attributes:
        MAX_RECORDS: 最大记录数量（默认 100）
        history_file: 历史文件路径

    Example:
        >>> manager = HistoryManager("data/build_history.json")
        >>> manager.add(BuildRecord(...))
        >>> records = manager.list(env="dev")
        >>> stats = manager.stats()
    """

    # 类常量：最大保留记录数
    MAX_RECORDS = 100

    def __init__(
        self, history_file: str = "data/build_history.json", create: bool = True
    ):
        """
        初始化历史管理器

        Args:
            history_file: 历史文件路径
            create: 为 True 时确保文件存在（缺失则创建空文件与父目录）；
                只读查询场景应传 False，复用已存在的锁文件（历史文件存在时），
                不会创建新的数据文件
        """
        self.history_file = Path(history_file)
        if create:
            # 确保文件存在
            self._ensure_file()

    # ========================================================================
    # 私有方法：文件操作
    # ========================================================================

    def _ensure_file(self):
        """
        确保历史文件存在

        如果文件不存在，创建空的历史文件。
        同时创建父目录。

        Note:
            写入同样在文件锁内进行，避免与其他进程的首次初始化互相覆盖
        """
        if not self.history_file.exists():
            # 创建父目录（如果不存在）
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(self.history_file):
                # 二次确认：可能已被其他进程创建
                if not self.history_file.exists():
                    self._write_records([])

    def _read_records(self, allow_backup: bool = False) -> list[dict]:
        """
        读取所有历史记录

        Args:
            allow_backup: 为 True 时允许把损坏文件搬走备份（仅写路径持锁时可用）；
                只读查询必须保持 False，确保查询无副作用

        Returns:
            记录字典列表

        Note:
            文件不存在时返回空列表；内容损坏（非法 JSON 或顶层不是对象）时，
            写路径会先备份为 <name>.corrupt 再返回空列表，
            避免后续写入把损坏内容连带真实历史一起丢弃
        """
        try:
            with open(self.history_file, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            return self._handle_corrupt(allow_backup, "内容不是合法 JSON")

        if not isinstance(data, dict):
            return self._handle_corrupt(allow_backup, "顶层结构不是 JSON 对象")

        records = data.get("records", [])
        if not isinstance(records, list):
            return self._handle_corrupt(allow_backup, "records 字段不是数组")
        return records

    def _handle_corrupt(self, allow_backup: bool, reason: str) -> list[dict]:
        """
        处理损坏的历史文件

        Args:
            allow_backup: 为 True 时把损坏文件搬走备份，否则仅告警
            reason: 判定为损坏的原因，用于日志

        Returns:
            空的记录列表

        Example:
            >>> manager._handle_corrupt(False, "内容不是合法 JSON")  # doctest: +SKIP
            []
        """
        if allow_backup:
            self._backup_corrupt()
            log_warn(f"历史文件损坏（{reason}），已备份为 {self.history_file.name}.corrupt")
        else:
            log_warn(f"历史文件损坏（{reason}），本次按空历史处理（只读查询不做备份）")
        return []

    def _backup_corrupt(self) -> None:
        """
        将损坏的历史文件另存为 <name>.corrupt

        Note:
            备份自身失败时静默跳过，不阻断读取流程
        """
        try:
            backup = self.history_file.parent / f"{self.history_file.name}.corrupt"
            os.replace(self.history_file, backup)
        except OSError:
            pass

    def _write_records(self, records: list[dict]):
        """
        写入所有历史记录

        Args:
            records: 记录字典列表

        Note:
            - 落盘细节（临时文件 + fsync + os.replace 原子替换）复用
              jenkins_config.filelock.atomic_write，与配置文件保存保持一致
            - ensure_ascii=False 支持中文
            - indent=2 美化输出
        """
        atomic_write(
            self.history_file,
            lambda f: json.dump(
                {"records": records},
                f,
                ensure_ascii=False,  # 支持中文
                indent=2,  # 缩进美化
            ),
        )


    # ========================================================================
    # 公共方法：记录管理
    # ========================================================================

    def add(self, record: BuildRecord):
        """
        添加构建记录

        将新记录添加到列表开头（最新的在前），并限制总数。

        Args:
            record: 构建记录对象

        Note:
            - 新记录插入到开头（index 0）
            - 超过 MAX_RECORDS 的旧记录会被丢弃

        Example:
            >>> manager.add(BuildRecord(
            ...     timestamp="2026-03-20T10:00:00",
            ...     env="dev",
            ...     job_key="dev_project",
            ...     build_num=123,
            ...     status="SUCCESS",
            ...     duration=60,
            ...     log_file="/logs/dev_project_#123.log"
            ... ))
        """
        # 读-改-写全程持锁，避免与 CLI / MCP Server 等其他进程互相覆盖
        with file_lock(self.history_file, required=True):
            # 读取现有记录（持锁写路径，允许备份损坏文件）
            records = self._read_records(allow_backup=True)

            # 新记录插入到开头（最新的在前）
            # asdict() 将 dataclass 转换为字典
            records.insert(0, asdict(record))


            # 限制记录数量，保留最新的 MAX_RECORDS 条
            records = records[: self.MAX_RECORDS]

            # 写回文件
            self._write_records(records)

    def add_batch(self, records: list[BuildRecord]):
        """
        批量添加构建记录

        一次读取、批量插入、一次写回，比循环调用 add() 更高效。

        Args:
            records: 构建记录对象列表

        Note:
            - 新记录整体插入到开头（最新的在前）
            - 超过 MAX_RECORDS 的旧记录会被丢弃
        """
        if not records:
            return

        # 读-改-写全程持锁，避免与 CLI / MCP Server 等其他进程互相覆盖
        with file_lock(self.history_file, required=True):
            # 读取现有记录（持锁写路径，允许备份损坏文件）
            existing = self._read_records(allow_backup=True)


            # 将新记录转换为字典并整体插入到开头
            new_dicts = [asdict(r) for r in records]
            combined = new_dicts + existing

            # 限制记录数量
            combined = combined[: self.MAX_RECORDS]

            # 一次写回文件
            self._write_records(combined)

    def list(self, env: str | None = None, limit: int = 20) -> list[BuildRecord]:
        """
        查询历史记录

        Args:
            env: 按环境过滤，为 None 时返回所有记录
            limit: 返回的最大数量

        Returns:
            BuildRecord 列表（按时间倒序）

        Example:
            # 获取最近 20 条记录
            >>> records = manager.list()

            # 获取 dev 环境的最近 10 条记录
            >>> records = manager.list(env="dev", limit=10)

        Note:
            读取同样持锁，避免在写入方执行 os.replace 的瞬间读到（或占用）目标文件
        """
        with file_lock(self.history_file, create=False):
            records = self._read_records()

        # 按环境过滤
        if env:
            records = [r for r in records if r.get("env") == env]

        # 转换为 BuildRecord 对象并限制数量
        return [BuildRecord(**r) for r in records[:limit]]

    def stats(self) -> dict:
        """
        统计构建历史

        计算总构建数、成功数、失败数和成功率。

        Returns:
            统计结果字典：
            {
                "total": 总构建数,
                "success": 成功数,
                "failure": 失败数,
                "building": 未落终态的占位记录数,
                "other": 既非成功也非失败的其他终态数（如 ABORTED / CANCELLED）,
                "success_rate": 成功率（百分比，分母仅含 success + failure）
            }

        Note:
            - MCP Server 触发时写入的 BUILDING 占位记录不会自动更新为终态，
              因此不参与成功率计算，仅在 building 字段中单独统计
            - ABORTED / CANCELLED 等既不算成功也不算失败的状态计入 other，
              同样不参与成功率分母，保证 success + failure == 分母

        Example:
            >>> stats = manager.stats()
            >>> print(f"成功率: {stats['success_rate']}%")
        """
        with file_lock(self.history_file, create=False):
            records = self._read_records()

        total = len(records)
        success = sum(1 for r in records if r.get("status") == "SUCCESS")
        failure = sum(1 for r in records if r.get("status") in ("FAILURE", "TIMEOUT"))
        building = sum(1 for r in records if r.get("status") == "BUILDING")

        # 成功率分母只含已落终态且语义明确的记录，
        # BUILDING 占位记录与 ABORTED / CANCELLED 等状态都不参与
        settled = success + failure
        other = total - settled - building

        return {
            "total": total,
            "success": success,
            "failure": failure,
            "building": building,
            "other": other,
            # 计算成功率，避免除以零
            "success_rate": round(success / settled * 100, 1) if settled > 0 else 0,
        }

    def get_last_build_group(self) -> list[BuildRecord]:
        """
        获取最近一次构建的所有项目

        按时间戳分组，返回最近一次成功触发的所有构建记录
        （build_num > 0 表示成功触发）

        Returns:
            BuildRecord 列表（同一 timestamp 的所有项目）

        Example:
            >>> group = manager.get_last_build_group()
            >>> for r in group:
            ...     print(f"{r.job_key} #{r.build_num}")
        """
        with file_lock(self.history_file, create=False):
            records = self._read_records()
        if not records:
            return []

        last_timestamp = records[0].get("timestamp")
        group = [
            BuildRecord(**r)
            for r in records
            if r.get("timestamp") == last_timestamp and r.get("build_num", 0) > 0
        ]
        return group

    def clear(self):
        """
        清空所有历史记录

        删除所有记录，保留空的历史文件。
        """
        with file_lock(self.history_file, required=True):
            self._write_records([])

