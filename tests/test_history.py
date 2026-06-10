# tests/test_history.py
import pytest
import json
from pathlib import Path
from jenkins_config.history import HistoryManager, BuildRecord
from jenkins_config.jenkins import BuildStatus

def test_add_record(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    record = BuildRecord(
        timestamp="2026-03-20T10:00:00",
        env="dev",
        job_key="dev_test",
        build_num=123,
        status="SUCCESS",
        duration=60,
        log_file="/tmp/test.log"
    )

    manager.add(record)

    # 验证文件写入
    data = json.loads(history_file.read_text())
    assert len(data["records"]) == 1
    assert data["records"][0]["job_key"] == "dev_test"

def test_list_records(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    # 添加多条记录
    for i in range(3):
        manager.add(BuildRecord(
            timestamp=f"2026-03-20T10:0{i}:00",
            env="dev" if i < 2 else "test",
            job_key=f"dev_test_{i}",
            build_num=100 + i,
            status="SUCCESS",
            duration=60,
            log_file=""
        ))

    # 查询所有
    all_records = manager.list()
    assert len(all_records) == 3

    # 按环境过滤
    dev_records = manager.list(env="dev")
    assert len(dev_records) == 2

def test_stats(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    manager.add(BuildRecord(
        timestamp="", env="", job_key="", build_num=1,
        status="SUCCESS", duration=60, log_file=""
    ))
    manager.add(BuildRecord(
        timestamp="", env="", job_key="", build_num=2,
        status="FAILURE", duration=30, log_file=""
    ))

    stats = manager.stats()
    assert stats["total"] == 2
    assert stats["success"] == 1
    assert stats["failure"] == 1
    assert stats["success_rate"] == 50.0

def test_max_records_limit(tmp_path):
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.MAX_RECORDS = 5

    # 添加 10 条记录
    for i in range(10):
        manager.add(BuildRecord(
            timestamp=f"2026-03-20T10:00:{i:02d}",
            env="dev", job_key=f"job_{i}", build_num=i,
            status="SUCCESS", duration=60, log_file=""
        ))

    records = manager.list()
    assert len(records) == 5
    # 应该保留最新的 5 条
    assert records[0].job_key == "job_9"


# ============================================================================
# 边缘路径
# ============================================================================


def test_corrupted_history_file(tmp_path):
    """损坏的历史文件返回空列表"""
    history_file = tmp_path / "history.json"
    history_file.write_text("not valid json", encoding="utf-8")
    manager = HistoryManager(str(history_file))
    records = manager.list()
    assert records == []


def test_missing_history_file(tmp_path):
    """不存在的历史文件返回空列表"""
    manager = HistoryManager(str(tmp_path / "nonexistent.json"))
    records = manager.list()
    assert records == []


def test_get_last_build_group(tmp_path):
    """获取最后一次构建组"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    # 第一组
    manager.add(BuildRecord(
        timestamp="2026-06-10T10:00:00", env="dev",
        job_key="dev_app", build_num=1, status="SUCCESS",
        duration=60, log_file="",
    ))
    # 第二组
    manager.add(BuildRecord(
        timestamp="2026-06-10T11:00:00", env="dev",
        job_key="dev_app", build_num=2, status="SUCCESS",
        duration=60, log_file="",
    ))
    manager.add(BuildRecord(
        timestamp="2026-06-10T11:00:00", env="test",
        job_key="test_app", build_num=3, status="SUCCESS",
        duration=60, log_file="",
    ))

    group = manager.get_last_build_group()
    assert len(group) == 2  # 返回 [] 而不是 None
    # 两个记录的 timestamp 都是 11:00:00，所以都在组内
    assert {r.build_num for r in group} == {2, 3}


def test_get_last_build_group_empty(tmp_path):
    """空历史返回空列表"""
    manager = HistoryManager(str(tmp_path / "empty.json"))
    assert manager.get_last_build_group() == []


def test_get_last_build_group_filters_zero_builds(tmp_path):
    """过滤构建编号为 0 的记录"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.add(BuildRecord(
        timestamp="2026-06-10T10:00:00", env="dev",
        job_key="dev_app", build_num=0, status="FAILURE",
        duration=0, log_file="",
    ))
    assert manager.get_last_build_group() == []


def test_clear_history(tmp_path):
    """清空历史记录"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.add(BuildRecord(
        timestamp="", env="", job_key="", build_num=1,
        status="SUCCESS", duration=60, log_file="",
    ))
    assert len(manager.list()) == 1

    manager.clear()
    assert len(manager.list()) == 0
