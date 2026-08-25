# jenkins_config/filelock.py
"""
文件互斥与原子写入模块 - 供多进程共享的落盘原语

CLI 与 MCP Server 可能同时运行，历史文件、配置文件都存在
"读-改-写" 竞争。本模块把两件事收敛到一处，避免各模块重复实现：

1. ``file_lock``：基于同目录 ``<name>.lock`` 哨兵文件的进程间排他锁
   （Windows 用 msvcrt.locking，POSIX 用 fcntl.flock，统一超时语义）
2. ``atomic_write``：先写带进程号的临时文件，fsync 后 os.replace 原子替换
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Callable, Iterator

from jenkins_config.utils import log_warn

# 锁等待上限（秒）：超时后放弃加锁并继续执行，避免死等
LOCK_TIMEOUT = 10.0

# 加锁重试间隔（秒）
LOCK_RETRY_INTERVAL = 0.05

if os.name == "nt":
    import msvcrt

    def _acquire(handle: IO[bytes]) -> bool:
        """在 Windows 上以非阻塞方式反复尝试加锁，直到超时

        Args:
            handle: 锁文件句柄

        Returns:
            成功取得锁返回 True，等待超过 LOCK_TIMEOUT 返回 False

        Note:
            msvcrt.locking 锁定的是当前文件指针处的字节，
            因此先 seek(0) 把锁区间固定在字节 0，避免不同进程锁到不同偏移
        """
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(LOCK_RETRY_INTERVAL)

    def _release(handle: IO[bytes]) -> None:
        """释放 Windows 文件锁"""
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _acquire(handle: IO[bytes]) -> bool:
        """在 POSIX 上以非阻塞方式反复尝试加排他锁，直到超时

        Args:
            handle: 锁文件句柄

        Returns:
            成功取得锁返回 True，等待超过 LOCK_TIMEOUT 返回 False

        Note:
            使用 LOCK_NB 轮询而非阻塞的 LOCK_EX，
            与 Windows 分支共用同一个超时语义，避免持锁进程挂死导致无限等待
        """
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(LOCK_RETRY_INTERVAL)

    def _release(handle: IO[bytes]) -> None:
        """释放 POSIX 文件锁"""
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def file_lock(
    target: Path, create: bool = True, required: bool = False
) -> Iterator[None]:
    """对目标文件加进程间排他锁

    锁本身落在同目录的 ``<name>.lock`` 哨兵文件上，不影响目标文件内容，
    使 CLI 与 MCP Server 等多个进程的"读-改-写"序列化，避免互相覆盖。

    Args:
        target: 需要保护的目标文件路径
        create: 为 True 时允许创建锁文件与父目录（写路径）；
            为 False 时若目标文件与锁文件都不存在则直接放行不加锁，
            不会创建任何新文件；若目标文件存在但锁文件尚不存在，
            仍会创建同目录的 ``<name>.lock`` 锁文件用于互斥
        required: 为 True 时（读-改-写等必须互斥的写路径）加锁失败即抛
            TimeoutError，由调用方决定重试或失败；为 False 时仅告警后降级放行

    Yields:
        None，退出上下文时自动释放锁

    Raises:
        TimeoutError: required 为 True 且等待超过 LOCK_TIMEOUT，
            或 required 为 True 但锁文件无法创建

    Example:
        >>> with file_lock(Path("data/build_history.json"), required=True):
        ...     pass  # 此块内对该文件的读改写是进程间互斥的
    """
    lock_path = target.parent / f"{target.name}.lock"
    if not create and not target.exists() and not lock_path.exists():
        # 只读场景且无文件可保护：不创建任何文件
        yield
        return

    try:
        if create:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
    except OSError as e:
        if required:
            raise TimeoutError(f"无法创建文件锁 {lock_path}: {e}") from e
        # 无法创建锁文件（如只读目录）时降级为不加锁，不阻断主流程
        log_warn(f"无法创建文件锁 {lock_path}（{e}），本次操作不加锁")
        yield
        return

    acquired = False
    try:
        acquired = _acquire(handle)
        if not acquired:
            message = (
                f"等待文件锁 {lock_path.name} 超时（{LOCK_TIMEOUT:.0f}s）"
            )
            if required:
                # 写路径不能在无锁状态下继续读-改-写，否则并发时丢失更新
                raise TimeoutError(message)
            log_warn(f"{message}，本次操作不加锁，与其他进程并发时可能互相覆盖")
        yield
    finally:
        if acquired:
            _release(handle)
        handle.close()



def atomic_write(
    target: Path, writer: Callable[[IO[str]], None], encoding: str = "utf-8"
) -> None:
    """以原子替换的方式写入文本文件

    Args:
        target: 目标文件路径（父目录需已存在）
        writer: 接收已打开的文本句柄并写入内容的回调
        encoding: 文件编码

    Raises:
        OSError: 写入或替换失败（此时临时文件已清理）
        BaseException: writer 回调抛出的任何异常都会先清理临时文件再向上传播

    Note:
        - 先写同目录临时文件再 os.replace 原子替换，
          避免写入中断留下被截断的损坏文件
        - 临时文件名带进程号，避免多进程写到同一个临时文件互相覆盖

    Example:
        >>> atomic_write(Path("out.txt"), lambda f: f.write("hi"))  # doctest: +SKIP
    """
    tmp_path = target.parent / f"{target.name}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding=encoding) as f:
            writer(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        # 写入或替换失败（如 Windows 上目标被其他进程占用、
        # writer 回调抛 TypeError 等非 OSError 异常）时清理临时文件再抛出，
        # 捕获 BaseException 确保任何异常路径都不残留临时文件
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
