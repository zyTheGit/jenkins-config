# tests/test_utils.py
import sys
from io import StringIO
from jenkins_config.utils import format_duration, print_sep

def test_format_duration():
    assert format_duration(0) == "0分0秒"
    assert format_duration(65) == "1分5秒"
    assert format_duration(120) == "2分0秒"

def test_print_sep(capsys):
    print_sep("-")
    captured = capsys.readouterr()
    assert "-" * 80 in captured.err