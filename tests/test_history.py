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
    """\u6e05\u7a7a\u5386\u53f2\u8bb0\u5f55"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.add(BuildRecord(
        timestamp="", env="", job_key="", build_num=1,
        status="SUCCESS", duration=60, log_file="",
    ))
    assert len(manager.list()) == 1

    manager.clear()
    assert len(manager.list()) == 0


# ============================================================================
# add_batch \u6279\u91cf\u64cd\u4f5c
# ============================================================================


def test_add_batch_inserts_at_front(tmp_path):
    """\u6279\u91cf\u63d2\u5165\u540e\u65b0\u8bb0\u5f55\u4f4d\u4e8e\u5f00\u5934\uff08\u6700\u65b0\u7684\u5728\u524d\uff09"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    # \u5148\u6dfb\u52a0\u4e00\u6761\u65e7\u8bb0\u5f55
    manager.add(BuildRecord(
        timestamp="2026-03-20T09:00:00", env="dev",
        job_key="dev_old", build_num=1,
        status="SUCCESS", duration=60, log_file="",
    ))

    # \u6279\u91cf\u6dfb\u52a0\u4e24\u6761\u65b0\u8bb0\u5f55
    new_records = [
        BuildRecord(
            timestamp="2026-03-20T10:00:00", env="dev",
            job_key="dev_new_1", build_num=2,
            status="SUCCESS", duration=30, log_file="",
        ),
        BuildRecord(
            timestamp="2026-03-20T10:01:00", env="dev",
            job_key="dev_new_2", build_num=3,
            status="FAILURE", duration=45, log_file="",
        ),
    ]
    manager.add_batch(new_records)

    records = manager.list()
    assert len(records) == 3
    # \u65b0\u8bb0\u5f55\u5e94\u5728\u5f00\u5934\uff0c\u4fdd\u6301\u539f\u59cb\u987a\u5e8f
    assert records[0].job_key == "dev_new_1"
    assert records[1].job_key == "dev_new_2"
    # \u65e7\u8bb0\u5f55\u5e94\u5728\u672b\u5c3e
    assert records[2].job_key == "dev_old"


def test_add_batch_max_records_truncation(tmp_path):
    """\u6279\u91cf\u6dfb\u52a0\u540e\u8d85\u8fc7 MAX_RECORDS \u7684\u65e7\u8bb0\u5f55\u88ab\u622a\u65ad"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.MAX_RECORDS = 4

    # \u5148\u6dfb\u52a0 3 \u6761\u65e7\u8bb0\u5f55
    for i in range(3):
        manager.add(BuildRecord(
            timestamp=f"2026-03-20T09:0{i}:00", env="dev",
            job_key=f"dev_old_{i}", build_num=i,
            status="SUCCESS", duration=60, log_file="",
        ))

    # \u6279\u91cf\u6dfb\u52a0 3 \u6761\u65b0\u8bb0\u5f55\uff0c\u603b\u8ba1 6 \u6761\uff0c\u5e94\u622a\u65ad\u4e3a 4 \u6761
    new_records = [
        BuildRecord(
            timestamp=f"2026-03-20T10:0{i}:00", env="dev",
            job_key=f"dev_new_{i}", build_num=10 + i,
            status="SUCCESS", duration=30, log_file="",
        )
        for i in range(3)
    ]
    manager.add_batch(new_records)

    records = manager.list()
    assert len(records) == 4
    # \u65b0\u8bb0\u5f55\u5e94\u5168\u90e8\u4fdd\u7559
    assert {r.job_key for r in records} == {
        "dev_new_0", "dev_new_1", "dev_new_2", "dev_old_2"
    }
    # 新记录在前，dev_old_2 是最后添加的旧记录（最新），位于末尾
    assert records[0].job_key == "dev_new_0"
    assert records[3].job_key == "dev_old_2"


def test_add_batch_empty_list(tmp_path):
    """\u7a7a\u5217\u8868\u8f93\u5165\u4e0d\u62a5\u9519\uff0c\u4e14\u4e0d\u5f71\u54cd\u73b0\u6709\u8bb0\u5f55"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    manager.add(BuildRecord(
        timestamp="", env="", job_key="existing",
        build_num=1, status="SUCCESS", duration=60, log_file="",
    ))

    # \u4f20\u5165\u7a7a\u5217\u8868\uff0c\u4e0d\u5e94\u62a5\u9519
    manager.add_batch([])

    records = manager.list()
    assert len(records) == 1
    assert records[0].job_key == "existing"


# ============================================================================
# 文件安全：create 开关 / 原子写入 / 损坏备份
# ============================================================================


def _record(job_key="dev_test", status="SUCCESS"):
    return BuildRecord(
        timestamp="2026-03-20T10:00:00", env="dev", job_key=job_key,
        build_num=1, status=status, duration=60, log_file="",
    )


def test_create_false_does_not_touch_filesystem(tmp_path):
    """create=False 时只读查询不创建文件与父目录"""
    history_file = tmp_path / "sub" / "history.json"
    manager = HistoryManager(str(history_file), create=False)

    assert manager.list() == []
    assert manager.stats()["total"] == 0
    assert not history_file.exists()
    assert not history_file.parent.exists()


def test_corrupt_file_backed_up_on_write(tmp_path):
    """写入时发现损坏的 JSON：另存为 .corrupt，新记录正常落盘"""
    history_file = tmp_path / "history.json"
    history_file.write_text("{not json", encoding="utf-8")

    manager = HistoryManager(str(history_file))
    manager.add(_record("dev_new"))

    backup = tmp_path / "history.json.corrupt"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "{not json"
    assert [r.job_key for r in manager.list()] == ["dev_new"]


def test_corrupt_file_not_touched_by_readonly_query(tmp_path):
    """只读查询遇到损坏文件只告警，不搬走文件（无副作用）"""
    history_file = tmp_path / "history.json"
    history_file.write_text("{not json", encoding="utf-8")

    manager = HistoryManager(str(history_file), create=False)
    assert manager.list() == []

    assert history_file.exists()
    assert not (tmp_path / "history.json.corrupt").exists()


def test_non_object_json_treated_as_corrupt(tmp_path):
    """合法 JSON 但顶层不是对象时按损坏处理，不抛 AttributeError"""
    history_file = tmp_path / "history.json"
    history_file.write_text("[1, 2, 3]", encoding="utf-8")

    manager = HistoryManager(str(history_file), create=False)
    assert manager.list() == []
    assert manager.stats()["total"] == 0


def test_records_field_not_list_treated_as_corrupt(tmp_path):
    """records 字段不是数组时同样按损坏处理"""
    history_file = tmp_path / "history.json"
    history_file.write_text('{"records": {"a": 1}}', encoding="utf-8")

    manager = HistoryManager(str(history_file), create=False)
    assert manager.list() == []


def test_write_is_atomic_no_tmp_left(tmp_path):
    """写入后不残留临时文件，且内容是完整可解析的 JSON"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.add(_record())

    assert not (tmp_path / "history.json.tmp").exists()
    data = json.loads(history_file.read_text(encoding="utf-8"))
    assert len(data["records"]) == 1


def test_lock_file_released_after_write(tmp_path):
    """写入结束后锁已释放，同进程再次写入不会阻塞"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    manager.add(_record("dev_a"))
    manager.add(_record("dev_b"))

    assert {r.job_key for r in manager.list()} == {"dev_a", "dev_b"}


def test_stats_excludes_building_from_success_rate(tmp_path):
    """BUILDING 占位记录单独统计，不参与成功率分母"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    manager.add_batch([
        _record("dev_ok", "SUCCESS"),
        _record("dev_bad", "FAILURE"),
        _record("dev_wip", "BUILDING"),
    ])

    stats = manager.stats()
    assert stats["total"] == 3
    assert stats["building"] == 1
    # 分母为 2（剔除 BUILDING），成功 1 条
    assert stats["success_rate"] == 50.0


def test_stats_all_building_success_rate_zero(tmp_path):
    """全部为 BUILDING 时成功率为 0，不触发除零"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.add(_record("dev_wip", "BUILDING"))

    stats = manager.stats()
    assert stats["building"] == 1
    assert stats["success_rate"] == 0


def test_stats_excludes_aborted_from_success_rate(tmp_path):
    """ABORTED / CANCELLED 计入 other，不参与成功率分母"""
    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    manager.add_batch([
        _record("dev_ok", "SUCCESS"),
        _record("dev_bad", "FAILURE"),
        _record("dev_abort", "ABORTED"),
        _record("dev_cancel", "CANCELLED"),
    ])

    stats = manager.stats()
    assert stats["total"] == 4
    assert stats["other"] == 2
    # 分母仅为 success + failure = 2
    assert stats["success_rate"] == 50.0


def test_write_path_raises_when_lock_unavailable(tmp_path):
    """写路径拿不到文件锁时抛 TimeoutError，不在无锁状态下继续读-改-写"""
    from unittest.mock import patch

    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))

    with patch("jenkins_config.filelock._acquire", return_value=False):
        with pytest.raises(TimeoutError, match="等待文件锁"):
            manager.add(_record("dev_x", "SUCCESS"))


def test_read_path_degrades_when_lock_unavailable(tmp_path):
    """只读路径拿不到锁时降级放行，不影响查询"""
    from unittest.mock import patch

    history_file = tmp_path / "history.json"
    manager = HistoryManager(str(history_file))
    manager.add(_record("dev_x", "SUCCESS"))

    with patch("jenkins_config.filelock._acquire", return_value=False):
        assert len(manager.list()) == 1

