"""
MCP Tools - 零配置初始化（init_config）

回答"我什么都没配，怎么让这个 Server 开始能用"：在用户级目录或当前工作目录
生成一份配置模板，并把必填字段清单一并回传，客户端据此引导用户填 server.url /
server.token。

安全模型是**分级门控**（见 init_config docstring 的完整理由）：
- 目标不存在 → 直接创建，不要求 JENKINS_MCP_ALLOW_WRITE；
- 目标已存在 + overwrite=false → 一律拒绝，与门控状态无关；
- overwrite=true → 属于改动既有资产，必须先开 JENKINS_MCP_ALLOW_WRITE。

模板里的凭据字段恒为占位符：绝不从环境变量或其它配置文件"猜"真实 token，
否则一个只想生成模板的调用会把别处的凭据复制到新文件里。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jenkins_config.config_io import template_fields, template_text
from jenkins_config.filelock import atomic_write, file_lock
from jenkins_config.mcp.errors import ErrorCode, failure_payload
from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import (
    CONFIG_ROOTS_ENV_VAR,
    backup_config_file,
    resolve_config_path,
    write_allowed,
    write_denied_message,
)
from jenkins_config.paths import CONFIG_FILE_NAMES, user_config_dir

# 允许的写入目标（刻意不接受任意路径参数：调用方可控的路径参数等于把
# "往哪写文件"的决定权交给客户端，白名单校验只能事后补救）
TARGETS = ("user", "cwd")

# 本次支持的模板格式（与 save_config 的 YAML-only 约束一致）
SUPPORTED_FORMATS = ("yaml",)

# 生成的配置文件名（取自 paths 的探测顺序首位，保证生成即可被自动探测到）
DEFAULT_CONFIG_FILE_NAME = CONFIG_FILE_NAMES[0]


def _failure(
    code: str, error: str, path: str = "", next_steps: list[str] | None = None
) -> dict[str, Any]:
    """组装 init_config 的失败返回体

    统一补 created=False：调用方只读 created 就能判断"这次到底写没写"，
    不必先分辨有没有 error_code。

    Args:
        code: ErrorCode 中的错误码
        error: 人类可读的错误描述
        path: 目标配置文件的绝对路径，未解析出来时留空
        next_steps: 显式指定的下一步动作，为 None 时按 code 取默认动作

    Returns:
        failure_payload() 的五字段字典，额外含 created 与 path

    Example:
        >>> _failure(ErrorCode.INVALID_TARGET, "target 非法")["created"]
        False
    """
    payload = failure_payload(code, error, path, next_steps)
    payload["created"] = False
    payload["path"] = path
    return payload


def _target_dir(target: str) -> Path:
    """把 target 折算为目标目录

    Args:
        target: 'user'（用户级配置目录）或 'cwd'（进程当前工作目录）

    Returns:
        目标目录（可能尚不存在）

    Raises:
        RuntimeError: target 为 'user' 但 HOME / USERPROFILE 均缺失

    Example:
        >>> _target_dir("cwd") == Path.cwd()
        True
    """
    if target == "user":
        return user_config_dir()
    return Path.cwd()


def _write_template(target_path: Path, fmt: str, overwrite: bool) -> str:
    """把模板写入目标文件，已存在则先留备份

    存在性复查、备份、写入三步都在**同一个 file_lock 临界区**内：
    调用方在加锁前做的 exists() 只是一张快照，若在窗口内有别的进程
    （CLI 或另一次 init_config）创建了同名文件，仅凭快照就会把它覆盖掉，
    绕过"默认调用永不损坏已有配置"这条约定。备份同理——放在锁外时，
    并发覆写会把"已经被替换成模板的文件"备份成所谓的原文件。

    Args:
        target_path: 目标配置文件路径（父目录会被创建）
        fmt: 模板格式（调用方已校验并归一化）
        overwrite: 为 False 时，持锁复查发现文件已存在即抛 FileExistsError

    Returns:
        备份文件的绝对路径；目标原本不存在时返回空字符串

    Raises:
        FileExistsError: overwrite 为 False 但持锁后发现目标已存在
        OSError: 目录创建、备份或写入失败
        TimeoutError: 等待文件锁超时（required=True）

    Example:
        >>> _write_template(Path("jenkins-config.yaml"), "yaml", False)  # doctest: +SKIP
        ''
    """
    content = template_text(fmt)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with file_lock(target_path, required=True):
        if target_path.exists() and not overwrite:
            raise FileExistsError(str(target_path))
        backup = backup_config_file(target_path)
        atomic_write(target_path, lambda handle: handle.write(content))
    return backup


@mcp.tool()
def init_config(
    target: str = "user", overwrite: bool = False, format: str = "yaml"
) -> dict[str, Any]:
    """在用户级目录或当前工作目录生成配置模板（零配置起步入口）

    只接受 target='user' / 'cwd' 两个枚举值，**不接受任意路径**：路径参数由
    客户端可控，等于把"往哪写文件"的决定权交出去，白名单只能事后补救。

    写权限采用分级门控，而不是一刀切：
    - 目标不存在 + overwrite=false（默认）→ 直接创建，不要求
      JENKINS_MCP_ALLOW_WRITE。它保护的是一个尚不存在的文件，防护收益为零；
      而一律强制门控会让零配置用户从 1 步变 3 步（改 mcp.json → 重启客户端 →
      再调用），"从零到 list_environments 成功"这条主链路直接失效。
    - 目标已存在 + overwrite=false → 一律 config_exists，与门控状态无关：
      默认调用永远不可能损坏用户已有配置。
    - overwrite=true → 视为改动既有资产，必须 JENKINS_MCP_ALLOW_WRITE=1。
      反过来"一律不门控"的代价是：一次误传 overwrite=true 就能静默覆盖生产配置，
      而配置文件里通常就是唯一一份可用凭据。

    生成内容恒为占位符（server.url / server.token 取自 config_io.PLACEHOLDER_VALUES），
    绝不从环境变量或其它配置文件推断真实凭据。

    Args:
        target: 写入位置，'user' 为 ~/.jenkins-config，'cwd' 为进程当前工作目录
        overwrite: 为 True 时允许覆盖已存在的配置（需先开启写门控）
        format: 模板格式，本版本仅支持 'yaml'

    Returns:
        成功时含 created=True、path（绝对路径）、format、backup（覆盖时的 .bak
        路径）、template_fields（字段清单）、next_steps；失败时含 created=False、
        error_code、error、config_path、next_steps、docs

    Example:
        >>> init_config(target="cwd")["created"]  # doctest: +SKIP
        True
    """
    # 参数名沿用 format（已是对外 MCP schema 的一部分，改名等于破坏客户端调用），
    # 仅在函数内改用 normalized_format，不去覆盖内置 format 的语义
    normalized_target = target.strip().lower()
    if normalized_target not in TARGETS:
        return _failure(
            ErrorCode.INVALID_TARGET,
            f"target 只能是 {' / '.join(TARGETS)}，当前为: {target}",
            next_steps=[
                "改用 target='user' 写入用户级目录 ~/.jenkins-config",
                "或改用 target='cwd' 写入 MCP Server 进程的当前工作目录",
                "调用 where_config 查看两者分别对应哪个绝对路径",
            ],
        )

    normalized_format = format.strip().lower()
    if normalized_format not in SUPPORTED_FORMATS:
        return _failure(
            ErrorCode.INVALID_TARGET,
            f"format 只能是 {' / '.join(SUPPORTED_FORMATS)}，当前为: {format}",
            next_steps=[
                f"把 format 改为 {' / '.join(SUPPORTED_FORMATS)} 后重新调用 init_config",
                "配置文件的自动探测只认 YAML/JSON 后缀名，格式与文件名必须一致",
            ],
        )

    try:
        base_dir = _target_dir(normalized_target)
    except RuntimeError as exc:
        return _failure(
            ErrorCode.HOME_UNAVAILABLE,
            f"无法确定用户级配置目录: {exc}",
            next_steps=[
                "改用 target='cwd' 写入进程当前工作目录",
                "或在客户端 mcp.json 的 env 中设置 JENKINS_MCP_CONFIG 为配置文件绝对路径",
                "或为进程设置 HOME（Windows 为 USERPROFILE）环境变量后重试",
            ],
        )

    candidate = base_dir / DEFAULT_CONFIG_FILE_NAME
    try:
        resolved = resolve_config_path(str(candidate))
    except PermissionError as exc:
        return _failure(
            ErrorCode.CONFIG_PATH_DENIED,
            f"目标目录不在允许范围内: {exc}",
            str(candidate),
            [
                "改用 target='user' 写入用户级目录 ~/.jenkins-config",
                f"或在客户端 mcp.json 的 env 中把该目录追加到 {CONFIG_ROOTS_ENV_VAR}",
                "调用 where_config 查看当前允许的根目录列表",
            ],
        )

    target_path = Path(resolved)
    if target_path.exists() and not overwrite:
        return _failure(
            ErrorCode.CONFIG_EXISTS,
            f"配置文件已存在，未做任何改动: {resolved}",
            resolved,
            [
                "调用 where_config 查看现有配置的路径与来源",
                "确需覆盖时显式传 overwrite=true（并已设置 JENKINS_MCP_ALLOW_WRITE=1）",
            ],
        )

    if overwrite and not write_allowed():
        return _failure(
            ErrorCode.WRITE_NOT_ALLOWED,
            write_denied_message("覆盖已有配置文件"),
            resolved,
        )

    try:
        backup = _write_template(target_path, normalized_format, overwrite)
    except FileExistsError:
        # 持锁复查发现文件已存在：与加锁前的快照检查回同一个载荷，
        # 调用方不必区分"哪一次检查发现的"
        return _failure(
            ErrorCode.CONFIG_EXISTS,
            f"配置文件已存在，未做任何改动: {resolved}",
            resolved,
            [
                "调用 where_config 查看现有配置的路径与来源",
                "确需覆盖时显式传 overwrite=true（并已设置 JENKINS_MCP_ALLOW_WRITE=1）",
            ],
        )
    except ValueError as exc:
        # 兜底：format 已在入口校验过，这里只可能是 template_text 内部对格式的
        # 进一步拒绝；仍回结构化载荷而不是把裸异常抛给客户端
        return _failure(
            ErrorCode.INVALID_TARGET,
            str(exc),
            resolved,
            [
                f"把 format 改为 {' / '.join(SUPPORTED_FORMATS)} 后重新调用 init_config",
                "调用 where_config 确认目标路径的文件后缀",
            ],
        )
    except (OSError, TimeoutError) as exc:
        return _failure(
            ErrorCode.CONFIG_PERMISSION_DENIED,
            f"写入配置模板失败: {exc}",
            resolved,
            [
                "补齐该目录的写权限后重试",
                "或改用 target='user' 写入用户级目录 ~/.jenkins-config",
            ],
        )

    return {
        "created": True,
        "path": resolved,
        "format": normalized_format,
        "backup": backup,
        "template_fields": template_fields(),
        "next_steps": [
            f"编辑 {resolved}，把 server.url / server.token 从占位符改为真实取值",
            "按需增删 environments 下的环境与项目",
            "调用 doctor 确认 config_complete 变为 ok",
            "调用 list_environments 验证配置已生效",
        ],
    }
