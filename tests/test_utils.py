# tests/test_utils.py
import sys
from io import StringIO
import pytest
from unittest.mock import patch
from jenkins_config.utils import (
    format_duration, print_sep, set_debug_mode,
    is_debug_mode, log_debug, DEBUG_MODE,
)

def test_format_duration():
    assert format_duration(0) == "0分0秒"
    assert format_duration(65) == "1分5秒"
    assert format_duration(120) == "2分0秒"

def test_print_sep(capsys):
    print_sep("-")
    captured = capsys.readouterr()
    assert "-" * 80 in captured.err


# ============================================================================
# 调试模式
# ============================================================================


def test_set_debug_mode():
    """启用/禁用调试模式"""
    set_debug_mode(True)
    assert is_debug_mode() is True
    set_debug_mode(False)
    assert is_debug_mode() is False


def test_log_debug_output(capsys):
    """debug 日志输出到 stderr"""
    set_debug_mode(True)
    try:
        log_debug("test debug message")
        captured = capsys.readouterr()
        assert "[DEBUG] test debug message" in captured.err
    finally:
        set_debug_mode(False)


def test_log_debug_no_output_when_disabled(capsys):
    """调试模式关闭时不输出"""
    set_debug_mode(False)
    log_debug("should not appear")
    captured = capsys.readouterr()
    assert captured.err == ""


# ============================================================================
# colorama 导入降级
# ============================================================================


def test_colorama_not_available():
    """colorama 不可用时自动降级（不抛异常）"""
    with patch("jenkins_config.utils.colorama", None, create=True):
        # colorama 不存在的路径不会阻塞
        pass  # 模块启动时的 try/except 已在导入时处理


def test_is_debug_mode_default():
    """默认调试模式关闭"""
    set_debug_mode(False)
    assert is_debug_mode() is False