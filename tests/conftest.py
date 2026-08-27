# tests/conftest.py
"""测试共享夹具

这里只放"多个测试文件都要做同一件事"的隔离逻辑。目前唯一一件是把
`paths.user_config_dir()` 指到临时目录：候选目录末位固定为 `~/.jenkins-config`，
不隔离的话开发机上真实存在的那份配置会被探测到，回退分支的断言就会随机器状态漂移。

同一件事此前在 test_cli 与 test_mcp/test_config_tools 各写了一遍，机制还不一样
（patch 上下文管理器 vs monkeypatch）。两种机制都还需要——前者要和别的 patch 并列
写进同一个 with，后者要在函数体里直接生效——所以这里保留两个薄夹具，
但"临时目录叫什么、补的是哪个符号"只有一处定义。

tests/test_paths.py 的 `_isolate_bases` 不在此列：它一次性隔离项目根 / CWD /
用户级目录三者，属于另一件事，只是顺带包含了这一步。
"""

from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

import pytest

# 临时用户级配置目录的目录名
USER_DIR_NAME = "userconfig"

# 被补的符号：paths 模块内的 user_config_dir
_TARGET = "jenkins_config.paths.user_config_dir"


@pytest.fixture
def isolated_user_dir(tmp_path: Path) -> Any:
    """把用户级候选目录指到临时目录（未进入的上下文管理器）

    Args:
        tmp_path: pytest 临时目录

    Returns:
        patch 上下文管理器，进入后 user_config_dir() 返回 tmp_path/userconfig

    Example:
        >>> # with isolated_user_dir: ...
    """
    return patch(_TARGET, return_value=tmp_path / USER_DIR_NAME)


@pytest.fixture
def patched_user_dir(monkeypatch: Any, tmp_path: Path) -> Iterator[Path]:
    """把用户级候选目录指到临时目录（进入测试体即已生效）

    Args:
        monkeypatch: pytest 猴子补丁夹具
        tmp_path: pytest 临时目录

    Yields:
        被指向的临时用户级配置目录（可能尚不存在）

    Example:
        >>> # def test_x(patched_user_dir): ...
    """
    from jenkins_config import paths

    user_dir = tmp_path / USER_DIR_NAME
    monkeypatch.setattr(paths, "user_config_dir", lambda: user_dir)
    yield user_dir
