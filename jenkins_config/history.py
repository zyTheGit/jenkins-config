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
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


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
    """
    timestamp: str
    env: str
    job_key: str
    build_num: int
    status: str
    duration: int
    log_file: str


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
    
    def __init__(self, history_file: str = "data/build_history.json"):
        """
        初始化历史管理器
        
        Args:
            history_file: 历史文件路径
        """
        self.history_file = Path(history_file)
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
        """
        if not self.history_file.exists():
            # 创建父目录（如果不存在）
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            # 写入空的记录列表
            self._write_records([])
    
    def _read_records(self) -> list[dict]:
        """
        读取所有历史记录
        
        Returns:
            记录字典列表
        
        Note:
            如果文件损坏或不存在，返回空列表
        """
        try:
            with open(self.history_file, encoding="utf-8") as f:
                data = json.load(f)
                return data.get("records", [])
        except (json.JSONDecodeError, FileNotFoundError):
            # 文件损坏或不存在时返回空列表
            return []
    
    def _write_records(self, records: list[dict]):
        """
        写入所有历史记录
        
        Args:
            records: 记录字典列表
        
        Note:
            - ensure_ascii=False 支持中文
            - indent=2 美化输出
        """
        with open(self.history_file, "w", encoding="utf-8") as f:
            json.dump(
                {"records": records},
                f,
                ensure_ascii=False,  # 支持中文
                indent=2             # 缩进美化
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
        # 读取现有记录
        records = self._read_records()
        
        # 新记录插入到开头（最新的在前）
        # asdict() 将 dataclass 转换为字典
        records.insert(0, asdict(record))
        
        # 限制记录数量，保留最新的 MAX_RECORDS 条
        records = records[:self.MAX_RECORDS]
        
        # 写回文件
        self._write_records(records)
    
    def list(self, env: Optional[str] = None, limit: int = 20) -> list[BuildRecord]:
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
        """
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
                "success_rate": 成功率（百分比）
            }
        
        Example:
            >>> stats = manager.stats()
            >>> print(f"成功率: {stats['success_rate']}%")
        """
        records = self._read_records()
        
        total = len(records)
        success = sum(1 for r in records if r.get("status") == "SUCCESS")
        failure = sum(1 for r in records if r.get("status") in ("FAILURE", "TIMEOUT"))
        
        return {
            "total": total,
            "success": success,
            "failure": failure,
            # 计算成功率，避免除以零
            "success_rate": round(success / total * 100, 1) if total > 0 else 0
        }
    
    def clear(self):
        """
        清空所有历史记录
        
        删除所有记录，保留空的历史文件。
        """
        self._write_records([])