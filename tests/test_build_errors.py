"""
构建错误处理模块测试

测试覆盖：
- save_error_log trigger_failed / queue_timeout
- extract_error_lines 各种日志输入
"""

from pathlib import Path

from jenkins_config.config import Job
from jenkins_config.build_errors import save_error_log, extract_error_lines


# ============================================================================
# save_error_log
# ============================================================================


def test_save_error_log_trigger_failed(tmp_path):
    """触发失败错误日志"""
    job = Job(key="dev_app", path="my-app", branch="main", params={"BRANCH": "main"}, env="dev")
    log_file = save_error_log(
        str(tmp_path),
        job,
        error_type="trigger_failed",
        error_msg="触发构建失败",
        base_url="http://jenkins:8080",
    )

    assert log_file.endswith("_error.log")
    path = Path(log_file)
    assert path.exists()

    content = path.read_text(encoding="utf-8")
    assert "构建错误日志" in content
    assert "trigger_failed" in content
    assert "触发构建失败" in content
    assert "my-app" in content
    assert "http://jenkins:8080" in content
    assert "检查 Jenkins Job 是否存在" in content


def test_save_error_log_queue_timeout(tmp_path):
    """队列超时错误日志"""
    job = Job(key="dev_app", path="my-app", branch="main", params={"BRANCH": "main"}, env="dev")
    log_file = save_error_log(
        str(tmp_path),
        job,
        error_type="queue_timeout",
        error_msg="获取构建编号超时",
        extra_info="队列URL: http://jenkins/queue/item/1/",
    )

    content = Path(log_file).read_text(encoding="utf-8")
    assert "queue_timeout" in content
    assert "检查 Jenkins 执行器是否繁忙" in content
    assert "队列URL" in content


def test_save_error_log_without_base_url(tmp_path):
    """不带 base_url 时不在日志中添加链接"""
    job = Job(key="dev_app", path="my-app", branch="main", params={}, env="dev")
    log_file = save_error_log(
        str(tmp_path),
        job,
        error_type="trigger_failed",
        error_msg="失败",
    )

    content = Path(log_file).read_text(encoding="utf-8")
    assert "Jenkins 链接" not in content


def test_save_error_log_with_params(tmp_path):
    """参数信息写入错误日志"""
    job = Job(
        key="dev_app", path="my-app", branch="feature",
        params={"BRANCH": "feature", "SKIP_TESTS": "true"}, env="dev",
    )
    log_file = save_error_log(str(tmp_path), job, error_type="trigger_failed", error_msg="失败")

    content = Path(log_file).read_text(encoding="utf-8")
    assert "构建参数" in content
    assert "BRANCH: feature" in content
    assert "SKIP_TESTS: true" in content


# ============================================================================
# extract_error_lines
# ============================================================================


def test_extract_error_lines_finds_keywords():
    """从日志中提取错误行"""
    log = """Started by user admin
[INFO] Building project
ERROR: build failed
[INFO] Cleaning up
FAILURE: tests not passed
"""
    lines = extract_error_lines(log)
    assert "ERROR: build failed" in lines
    assert "FAILURE: tests not passed" in lines


def test_extract_error_lines_no_keywords():
    """无错误关键词时返回最后 30 行"""
    log = "line1\nline2\nline3\n"
    lines = extract_error_lines(log)
    assert len(lines) == 3


def test_extract_error_lines_empty():
    """空日志返回空列表"""
    assert extract_error_lines("") == []
    assert extract_error_lines(None) == []


def test_extract_error_lines_case_insensitive():
    """大小写不敏感匹配"""
    log = "Error: something failed\n[INFO] build successful"
    lines = extract_error_lines(log)
    assert "Error: something failed" in lines
    assert "build successful" not in lines


def test_extract_error_lines_max_lines():
    """不超过最大行数限制"""
    log = "\n".join([f"ERROR line {i}" for i in range(100)])
    lines = extract_error_lines(log, max_lines=10)
    assert len(lines) == 10


def test_extract_error_lines_makefile():
    """Makefile 相关错误"""
    log = "make: *** [target] Error 1"
    lines = extract_error_lines(log)
    assert len(lines) == 1


def test_extract_error_lines_npm_err():
    """npm ERR 错误"""
    log = "npm ERR! code ENOENT\nnpm ERR! syscall open"
    lines = extract_error_lines(log)
    assert len(lines) == 2
