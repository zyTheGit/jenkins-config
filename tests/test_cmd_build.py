"""
构建命令模块测试

测试覆盖：
- _cleanup_old_logs 日志清理
- generate_report 报告生成

注意：run_build / run_rebuild_last 相关测试在 test_cmd_build_run.py
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, ANY

import pytest

from jenkins_config.cmd_build import _cleanup_old_logs, generate_report
from jenkins_config.build_result import BuildResult
from jenkins_config.jenkins import BuildStatus


def _create_log_dir(root: Path, days_ago: int, name: str = None) -> Path:
    """创建指定天数的旧日志目录"""
    date_str = (datetime.now() - timedelta(days=days_ago)).strftime("%Y%m%d")
    dir_name = name or f"build_{date_str}"
    d = root / dir_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "test.log").write_text("log content")
    return d


def test_cleanup_removes_old_logs(tmp_path):
    """清理超过保留天数的日志目录"""
    _create_log_dir(tmp_path, days_ago=1)   # 1 天前 -> 保留
    _create_log_dir(tmp_path, days_ago=5)   # 5 天前 -> 删除
    _create_log_dir(tmp_path, days_ago=10)  # 10 天前 -> 删除

    _cleanup_old_logs(str(tmp_path), retention_days=3)

    dirs = sorted(d.name for d in tmp_path.iterdir() if d.is_dir())
    # 只有 1 天前的保留（5天和10天被删）
    assert len(dirs) == 1


def test_cleanup_keeps_all_within_retention(tmp_path):
    """保留期内的日志全部保留"""
    _create_log_dir(tmp_path, days_ago=0)
    _create_log_dir(tmp_path, days_ago=1)
    _create_log_dir(tmp_path, days_ago=2)

    _cleanup_old_logs(str(tmp_path), retention_days=3)

    dirs = [d.name for d in tmp_path.iterdir() if d.is_dir()]
    assert len(dirs) == 3


def test_cleanup_skips_non_build_dirs(tmp_path):
    """非 build_ 前缀的目录不受影响"""
    _create_log_dir(tmp_path, days_ago=10, name="build_old")
    (tmp_path / "other_dir").mkdir()
    (tmp_path / ".keep").mkdir()

    _cleanup_old_logs(str(tmp_path), retention_days=3)

    dirs = sorted(d.name for d in tmp_path.iterdir() if d.is_dir())
    assert "other_dir" in dirs
    assert ".keep" in dirs
    # build_old 日期格式不合法，cleanup 会跳过（不删）
    assert "build_old" in dirs


def test_cleanup_non_existent_dir(tmp_path):
    """不存在的目录不报错"""
    _cleanup_old_logs(str(tmp_path / "nonexistent"), retention_days=3)
    # 不抛异常即通过


def test_cleanup_skips_files(tmp_path):
    """跳过目录中的普通文件"""
    f = tmp_path / "build_something.txt"
    f.write_text("not a dir")
    _cleanup_old_logs(str(tmp_path), retention_days=3)
    assert f.exists()


def test_cleanup_skips_invalid_date_format(tmp_path):
    """目录名日期格式不合法时跳过"""
    d = tmp_path / "build_abc"
    d.mkdir()
    _cleanup_old_logs(str(tmp_path), retention_days=3)
    assert d.exists()


# ============================================================================
# generate_report
# ============================================================================


def _make_result(job_key, status, build_num=1):
    """辅助创建 BuildResult"""
    return BuildResult(
        job_key=job_key, build_num=build_num, status=status,
        duration=60, log_file="/tmp/build.log",
    )


def test_generate_report_all_success(capsys):
    """全部成功"""
    results = [
        _make_result("dev_app", BuildStatus.SUCCESS, 1),
        _make_result("dev_web", BuildStatus.SUCCESS, 2),
    ]
    generate_report(results, "/tmp/logs")
    captured = capsys.readouterr()
    assert "构建结果汇总" in captured.err
    assert "总计: 2 个 Job" in captured.out
    assert "成功: 2 个" in captured.out
    assert "失败: 0 个" in captured.out
    assert "[OK] dev_app" in captured.out
    assert "[OK] dev_web" in captured.out


def test_generate_report_mixed(capsys):
    """混合成功/失败/中止/超时 - 有失败时 sys.exit(1)"""
    results = [
        _make_result("ok_job", BuildStatus.SUCCESS, 10),
        _make_result("fail_job", BuildStatus.FAILURE, 0),
        _make_result("abort_job", BuildStatus.ABORTED, 5),
        _make_result("timeout_job", BuildStatus.TIMEOUT, 0),
    ]
    with pytest.raises(SystemExit) as exc:
        generate_report(results, "/tmp/logs")
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "成功: 1 个" in captured.out
    assert "失败: 3 个" in captured.out
    assert "[FAIL] fail_job" in captured.out
    assert "[ABORT] abort_job" in captured.out
    assert "[TIMEOUT] timeout_job" in captured.out


def test_generate_report_exits_on_failure():
    """有失败构建时退出码为 1"""
    results = [_make_result("bad_job", BuildStatus.FAILURE)]
    with pytest.raises(SystemExit) as exc:
        generate_report(results, "/tmp/logs")
    assert exc.value.code == 1


def test_generate_report_unknown_status(capsys):
    """未知状态显示 [?]"""
    class UnknownStatus:
        value = "UNKNOWN_STATUS"
    results = [BuildResult(
        job_key="weird_job", build_num=99, status=UnknownStatus(),
        duration=0, log_file="",
    )]
    with pytest.raises(SystemExit):
        generate_report(results, "/tmp/logs")
    captured = capsys.readouterr()
    assert "[?] weird_job: UNKNOWN_STATUS (#99)" in captured.out
