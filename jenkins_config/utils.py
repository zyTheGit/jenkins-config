# jenkins_config/utils.py
"""
工具模块 - 提供日志输出、格式化等通用功能

这个模块包含了整个项目通用的工具函数，主要用于：
1. 彩色日志输出（INFO、SUCCESS、ERROR、WARN、DEBUG）
2. 分隔线打印
3. 时间格式化

ANSI 颜色码说明：
- \033[0;36m - 青色（cyan）
- \033[0;32m - 绿色（green）
- \033[0;31m - 红色（red）
- \033[0;33m - 黄色（yellow）
- \033[0;35m - 紫色（magenta）- DEBUG
- \033[0m - 重置颜色
"""

import sys


DEBUG_MODE = False


def set_debug_mode(enabled: bool):
    """
    设置调试模式

    Args:
        enabled: 是否启用调试模式
    """
    global DEBUG_MODE
    DEBUG_MODE = enabled


def is_debug_mode() -> bool:
    """
    检查是否处于调试模式

    Returns:
        True 如果调试模式已启用
    """
    return DEBUG_MODE


def log_info(msg: str):
    """
    输出信息日志（青色）

    用于输出一般性信息，如：正在执行的操作、状态更新等

    Args:
        msg: 要输出的消息内容

    Example:
        >>> log_info("正在触发构建：dev_project")
        [INFO] 正在触发构建：dev_project  # 青色显示
    """
    # \033[0;36m 设置青色，\033[0m 重置颜色
    # file=sys.stderr 确保输出到标准错误流，不影响管道操作
    print(f"\033[0;36m[INFO] {msg}\033[0m", file=sys.stderr)


def log_success(msg: str):
    """
    输出成功日志（绿色）

    用于输出成功信息，如：构建完成、操作成功等

    Args:
        msg: 要输出的消息内容

    Example:
        >>> log_success("构建完成：dev_project #123")
        [SUCCESS] 构建完成：dev_project #123  # 绿色显示
    """
    print(f"\033[0;32m[SUCCESS] {msg}\033[0m", file=sys.stderr)


def log_error(msg: str):
    """
    输出错误日志（红色）

    用于输出错误信息，如：构建失败、配置错误等

    Args:
        msg: 要输出的消息内容

    Example:
        >>> log_error("触发构建失败：dev_project")
        [ERROR] 触发构建失败：dev_project  # 红色显示
    """
    print(f"\033[0;31m[ERROR] {msg}\033[0m", file=sys.stderr)


def log_warn(msg: str):
    """
    输出警告日志（黄色）

    用于输出警告信息，如：构建中止、参数缺失等

    Args:
        msg: 要输出的消息内容

    Example:
        >>> log_warn("构建中止：dev_project #123")
        [WARN] 构建中止：dev_project #123  # 黄色显示
    """
    print(f"\033[0;33m[WARN] {msg}\033[0m", file=sys.stderr)


def log_debug(msg: str):
    """
    输出调试日志（紫色）

    仅在调试模式下输出，用于详细的调试信息，如：HTTP 请求详情、内部状态等

    Args:
        msg: 要输出的消息内容

    Example:
        >>> set_debug_mode(True)
        >>> log_debug("HTTP POST to /job/my-project/buildWithParameters")
        [DEBUG] HTTP POST to /job/my-project/buildWithParameters  # 紫色显示
    """
    if DEBUG_MODE:
        print(f"\033[0;35m[DEBUG] {msg}\033[0m", file=sys.stderr)


def print_sep(char: str = "="):
    """
    打印分隔线

    用于在输出中创建视觉分隔，帮助区分不同的输出区块

    Args:
        char: 分隔线使用的字符，默认为 "=""

    Example:
        >>> print_sep("-")
        --------------------------------------------------------------------------------
        >>> print_sep("*")
        ********************************************************************************
    """
    # 打印 80 个指定字符
    print(char * 80, file=sys.stderr)


def print_header(title: str):
    """
    打印标题头

    用于输出带边框的标题，格式为：
    ====================
    标题内容
    ====================

    Args:
        title: 标题内容

    Example:
        >>> print_header("构建结果汇总")
        ================================================================================
        构建结果汇总
        ================================================================================
    """
    print_sep()  # 上边框
    print(f"\033[0;36m{title}\033[0m", file=sys.stderr)  # 标题（青色）
    print_sep()  # 下边框


def format_duration(seconds: int) -> str:
    """
    格式化时长

    将秒数转换为易读的"X分X秒"格式

    Args:
        seconds: 秒数

    Returns:
        格式化后的时间字符串，如 "1分30秒"

    Example:
        >>> format_duration(0)
        '0分0秒'
        >>> format_duration(65)
        '1分5秒'
        >>> format_duration(120)
        '2分0秒'
    """
    # divmod(a, b) 返回 (a // b, a % b)，即商和余数
    # 例如：divmod(65, 60) = (1, 5)，表示 1 分 5 秒
    mins, secs = divmod(seconds, 60)
    return f"{mins}分{secs}秒"
