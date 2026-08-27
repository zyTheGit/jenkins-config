"""
MCP Tools - 零配置初始化（init_config）

回答"我什么都没配，怎么让这个 Server 开始能用"：在用户级目录（`~/.jenkins-config`）
或当前工作目录下的同名点目录（`<CWD>/.jenkins-config`）生成一份配置模板，
并把必填字段清单一并回传，客户端据此引导用户填 server.url / server.token。

安全模型是**分级门控**（见 init_config docstring 的完整理由）：
- 目标不存在 → 直接创建，不要求 JENKINS_MCP_ALLOW_WRITE；
- 目标已存在 + overwrite=false → 一律拒绝，与门控状态无关；
- overwrite=true → 属于改动既有资产，必须先开 JENKINS_MCP_ALLOW_WRITE。

模板里的凭据字段恒为占位符：绝不从环境变量或其它配置文件"猜"真实 token，
否则一个只想生成模板的调用会把别处的凭据复制到新文件里。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jenkins_config.config_io import template_fields, template_text
from jenkins_config.filelock import atomic_write, file_lock, lock_path_for
from jenkins_config.mcp.errors import ErrorCode, failure_payload
from jenkins_config.mcp.server import mcp
from jenkins_config.mcp.utils import (
    backup_config_file,
    resolve_config_path,
    write_allowed,
    write_denied_message,
    write_target_denied,
    write_target_denied_steps,
)
from jenkins_config.paths import (
    APP_DIR_NAME,
    CONFIG_FILE_NAMES,
    resolve_config_file,
    search_bases,
    user_config_dir,
)

logger = logging.getLogger(__name__)

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

    'cwd' 落在 `<CWD>/.jenkins-config` 而不是 CWD 顶层：这样项目级与用户级
    （`~/.jenkins-config`）目录结构完全一致，配置、data/、备份都收在同一个点目录里，
    加 .gitignore 也只需一条。该子目录同时是 paths.search_bases() 的首位候选，
    所以生成完即可被自动探测到。

    Args:
        target: 'user'（用户级配置目录）或 'cwd'（进程当前工作目录下的应用目录）

    Returns:
        目标目录（可能尚不存在）

    Raises:
        RuntimeError: target 为 'user' 但 HOME / USERPROFILE 均缺失

    Example:
        >>> _target_dir("cwd") == Path.cwd() / APP_DIR_NAME
        True
    """
    if target == "user":
        return user_config_dir()
    return Path.cwd() / APP_DIR_NAME


def _shadow_relation(target_path: Path) -> tuple[str, str]:
    """判断目标文件生成后与现有生效配置的遮蔽关系

    init_config 的落点只由 target 枚举折算，从不看探测结果；而
    `<CWD>/.jenkins-config` 的优先级高于同目录顶层的 `jenkins-config.yaml`。
    两件事凑在一起，一次"只是生成模板"的调用就能让一份填好真实 token 的配置
    连同它的 `data/build_history.json` 整体失效——文件还在，但再没有人读它。

    反向同样要回答：默认 target='user' 落在候选末位，只要项目级已有一份配置，
    新生成的文件从写出那一刻就不生效，此时若只说"已创建，去填 token 吧"，
    用户填完读到的仍是另一份配置。

    Args:
        target_path: 即将写入的目标配置文件路径

    Returns:
        二元组 (被本次生成顶掉的配置路径, 仍压在本次生成之上的配置路径)，
        两项都为绝对路径字符串，不存在对应关系时为空字符串；两者互斥

    Example:
        >>> _shadow_relation(Path.cwd() / "nowhere" / "jenkins-config.yaml")
        ('', '')
    """
    try:
        current = resolve_config_file().resolve()
    except (OSError, RuntimeError) as exc:
        logger.warning("探测现有配置失败（%s），跳过遮蔽检查", exc)
        return "", ""

    target = target_path.resolve()
    if not current.is_file() or current == target:
        return "", ""

    bases = [base.resolve() for base in search_bases()]

    def _rank(path: Path) -> tuple[int, int]:
        """按候选目录顺序 + 同目录内文件名顺序给配置文件打优先级（越小越优先）

        只比目录是不够的：resolve_config_file() 在同一个目录里还会按
        CONFIG_FILE_NAMES 依次取（yaml > yml > json），而本工具生成的恒是首位
        名字，因此同目录下已有的 .yml / .json 也会被顶掉。落在候选之外
        （如 JENKINS_MCP_CONFIG 指定的自定义文件名）时按末位处理。
        """
        try:
            dir_rank = bases.index(path.parent)
        except ValueError:
            dir_rank = len(bases)
        try:
            name_rank = CONFIG_FILE_NAMES.index(path.name)
        except ValueError:
            name_rank = len(CONFIG_FILE_NAMES)
        return dir_rank, name_rank

    target_rank = _rank(target)
    current_rank = _rank(current)
    if target_rank < current_rank:
        return str(current), ""
    return "", str(current)


def _discard_created_dir(directory: Path, lock_name: str) -> None:
    """回收本次为写入而新建、但最终一个字节都没写成的目录

    目录是这次调用凭空建出来的，里面只可能剩 file_lock 的哨兵文件
    （filelock 释放锁时不删哨兵）。哨兵留着 rmdir 就永远失败，于是
    "未做任何改动"的返回体旁边留下一个空的 `.jenkins-config`，
    它还会成为后续探测候选。只在目录里除哨兵之外别无他物时才回收，
    避免误删并发进程刚放进去的东西。

    Args:
        directory: 本次新建的目录
        lock_name: file_lock 哨兵文件名（`<目标文件名>.lock`）

    Returns:
        None

    Example:
        >>> _discard_created_dir(Path.cwd() / "not-created", "x.lock")
    """
    try:
        leftovers = list(directory.iterdir())
        if all(item.name == lock_name for item in leftovers):
            for item in leftovers:
                item.unlink()
            directory.rmdir()
    except OSError as exc:
        logger.debug("回收目录 %s 失败：%s", directory, exc)


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
    created_dir = not target_path.parent.exists()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with file_lock(target_path, required=True):
            if target_path.exists() and not overwrite:
                raise FileExistsError(str(target_path))
            backup = backup_config_file(target_path)
            atomic_write(target_path, lambda handle: handle.write(content))
    except TimeoutError:
        # 加锁超时意味着**另一个进程正持着这个哨兵**：此时回收会删掉它仍在使用的
        # 锁文件（POSIX 上 flock 绑 inode，删掉目录项后第三个进程能再建同名锁并
        # 同时加锁，互斥直接失效），还可能 rmdir 掉对方正要写入的目录。
        # 这条路径宁可留下一个空目录，也不动别人的锁。
        raise
    except Exception:
        if created_dir:
            _discard_created_dir(target_path.parent, lock_path_for(target_path).name)
        raise
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
    - 目标不存在，但生成后会**顶掉**另一份已生效的配置（如同目录顶层那份
      填了真实 token 的 jenkins-config.yaml）→ 同样 config_exists 并回报
      被遮蔽的路径：文件没被改动却再没人读它，与"损坏"没有区别。
    - overwrite=true → 视为改动既有资产，必须 JENKINS_MCP_ALLOW_WRITE=1。
      反过来"一律不门控"的代价是：一次误传 overwrite=true 就能静默覆盖生产配置，
      而配置文件里通常就是唯一一份可用凭据。

    生成内容恒为占位符（server.url / server.token 取自 config_io.PLACEHOLDER_VALUES），
    绝不从环境变量或其它配置文件推断真实凭据。

    Args:
        target: 写入位置，'user' 为 ~/.jenkins-config，'cwd' 为进程当前工作目录下的
            .jenkins-config（与用户级目录同构，因此历史与备份也收在同一个点目录里）
        overwrite: 为 True 时允许覆盖已存在的配置（需先开启写门控）
        format: 模板格式，本版本仅支持 'yaml'

    Returns:
        成功时含 created=True、path（绝对路径）、format、backup（覆盖时的 .bak
        路径）、shadowed_path（被本次生成顶掉的配置路径，无则为空串）、
        effective_path（写完后真正生效的配置路径，被更高优先级的配置压住时
        即那一份，否则就是 path）、template_fields（字段清单）、next_steps；
        失败时含 created=False、error_code、error、config_path、next_steps、docs

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
                f"或改用 target='cwd' 写入进程当前工作目录下的 {APP_DIR_NAME}/",
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
    if normalized_target == "cwd" and write_target_denied(candidate):
        try:
            user_config_dir()
        except RuntimeError as exc:
            # CWD 过宽 + 家目录不可用：此时"改用 target='user'"必然再失败一次，
            # 归到 home_unavailable，它的 next_steps 每条在该环境下都还可执行
            return _failure(
                ErrorCode.HOME_UNAVAILABLE,
                f"当前工作目录过宽（{Path.cwd()}），且无法确定用户级配置目录: {exc}",
                str(candidate),
            )
        return _failure(
            ErrorCode.CONFIG_PATH_DENIED,
            f"当前工作目录过宽（{Path.cwd()}），不在其中创建 {APP_DIR_NAME} 目录",
            str(candidate),
            write_target_denied_steps("改用 target='user' 写入用户级目录 ~/.jenkins-config"),
        )

    try:
        resolved = resolve_config_path(str(candidate))
    except PermissionError as exc:
        return _failure(
            ErrorCode.CONFIG_PATH_DENIED,
            f"目标目录不在允许范围内: {exc}",
            str(candidate),
            write_target_denied_steps("改用 target='user' 写入用户级目录 ~/.jenkins-config"),
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

    shadowed, shadowed_by = _shadow_relation(target_path)
    if shadowed and not overwrite:
        return _failure(
            ErrorCode.CONFIG_EXISTS,
            f"已有生效配置 {shadowed}，在 {resolved} 生成模板会让它整体失效，未做任何改动",
            resolved,
            [
                f"继续用现有配置：直接编辑 {shadowed}",
                "调用 where_config 确认现在生效的是哪一份配置",
                "确需改用该位置时显式传 overwrite=true（并已设置 JENKINS_MCP_ALLOW_WRITE=1）",
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
    except TimeoutError as exc:
        # 锁等待超时是并发占用，不是权限问题：给出"补写权限"这类动作只会把
        # 调用方引向一条修不好的路
        return _failure(
            ErrorCode.UNKNOWN_ERROR,
            f"等待配置文件锁超时，未做任何改动: {exc}",
            resolved,
            [
                "确认没有其他 CLI 或 MCP 进程正在写同一配置文件",
                "稍后重新调用 init_config",
                "调用 where_config 确认目标路径",
            ],
        )
    except OSError as exc:
        return _failure(
            ErrorCode.CONFIG_PERMISSION_DENIED,
            f"写入配置模板失败: {exc}",
            resolved,
            [
                "补齐该目录的写权限后重试",
                "或改用 target='user' 写入用户级目录 ~/.jenkins-config",
            ],
        )

    next_steps = [
        f"编辑 {resolved}，把 server.url / server.token 从占位符改为真实取值",
        "按需增删 environments 下的环境与项目",
        "调用 doctor 确认 config_complete 变为 ok",
        "调用 list_environments 验证配置已生效",
    ]
    if normalized_target == "cwd":
        # 工作目录常常就是 git 仓库，而这份文件里迟早会填进真实 token
        next_steps.insert(1, f"把 {APP_DIR_NAME}/ 加入 .gitignore，避免 token 被提交")
    if shadowed_by:
        # 文件写出来了，但优先级更高的另一份仍是生效配置：若不说清楚，
        # 用户会把 token 填进这份永远不被读取的文件，再回来问"为什么没生效"
        next_steps.insert(
            0,
            f"注意：当前生效的仍是 {shadowed_by}；要改用本文件，"
            f"需先移除或迁走 {shadowed_by}（可调用 where_config 复核）",
        )

    return {
        "created": True,
        "path": resolved,
        "format": normalized_format,
        "backup": backup,
        "shadowed_path": shadowed,
        "effective_path": shadowed_by or resolved,
        "template_fields": template_fields(),
        "next_steps": next_steps,
    }
