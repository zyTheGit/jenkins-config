# jenkins_config/config_io.py
"""
配置 I/O 模块 - 负责配置保存、序列化与模板生成

Config 接口（字段与方法）在 config.py，加载/校验在 config_load.py；
本模块只负责写出方向（保存、序列化、模板）。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config_types import (
    BuildConfig,
    Project,
    ServerConfig,
)
from .filelock import atomic_write, file_lock

if TYPE_CHECKING:
    from .config import Config


# 模板占位符取值（键为配置内的点分路径）
#
# 这些取值非空，能通过 config_load._validate_config，所以 Config.load 对一份
# "只 init 过、还没填"的配置会成功返回。也就是说"配置未完成"只能靠占位符比对
# 判定，不能靠加载是否抛错。判据与模板必须同源：若两处各写一份字面量，改了模板
# 忘改判据时 doctor 会把未填配置报成"已完成"，那比没有这项检查更糟。
PLACEHOLDER_VALUES: dict[str, str] = {
    "server.url": "http://your-jenkins-server:8080",
    "server.token": "your-api-token",
}

# YAML 配置文件的头部注释
#
# save_config（规范化回写）与 template_text（生成模板）都要写这段说明，
# 各写一份的话改了一处就会出现"同一个工具产出两种表头"。注释只在 YAML 生效，
# JSON 分支不带它——JSON 语法不支持注释，硬加会让文件无法被 json.loads 回读。
TEMPLATE_HEADER = (
    "# Jenkins 构建工具配置文件\n"
    "# 推荐使用 YAML 格式（支持注释）\n"
    "# 所有 Jenkins 构建参数都放在 params 字典中\n"
    "# 新增插件参数只需在 params 中添加，无需修改代码\n"
    "#\n"
    "# 参数合并优先级: 项目 params > 环境 params\n"
    "# 分支覆写: CLI -b 参数会覆盖 params 中 branch_field 指定的值\n\n"
)

# 模板字段说明的唯一来源：(点分键名, 说明, 是否必填)
#
# show_template()（CLI 打印）与 template_fields()（MCP init_config 返回）都从这里
# 渲染。两处各维护一份文案时，改了 CLI 说明而 MCP 返回体照旧，用户会拿到两套互相
# 矛盾的"必填项清单"；而 required 标记又直接决定 AI 客户端提示用户去填哪几个字段，
# 漂移的代价是零配置引导直接走错。
TEMPLATE_FIELD_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("server", "Jenkins 服务器配置", True),
    ("server.url", "Jenkins 地址", True),
    ("server.token", "API Token", True),
    ("server.username", "登录用户名（默认: admin）", False),
    ("build", "构建行为配置", False),
    ("build.mode", "parallel(默认) / sequential", False),
    ("build.poll_interval", "轮询间隔秒数（默认: 10）", False),
    ("build.queue_timeout", "队列等待超时秒数（默认: 30）", False),
    ("build.build_timeout", "构建超时秒数（默认: 3600）", False),
    ("build.curl_timeout", "HTTP 超时秒数（默认: 30）", False),
    ("build.log_dir", "日志目录（默认: ./jenkins_logs）", False),
    ("build.log_retention_days", "日志保留天数（默认: 3）", False),
    ("build.max_parallel", "并行构建上限（默认: 5）", False),
    ("branch_field", "CLI -b 使用的参数名（默认: branch），如 BRANCH、GIT_BRANCH", False),
    ("environments", "环境配置字典，键为环境名称", True),
    ("environments.<env>.description", "环境描述", False),
    ("environments.<env>.branch_field", "覆盖全局 branch_field", False),
    ("environments.<env>.params", "环境参数字典（新增插件只需加键值对）", False),
    ("environments.<env>.projects", "项目列表", True),
    ("environments.<env>.projects[].name", "项目名称", True),
    ("environments.<env>.projects[].path", "Job 路径（默认同 name）", False),
    ("environments.<env>.projects[].params", "项目参数（覆盖环境同名参数）", False),
)


# ============================================================================
# 保存和序列化
# ============================================================================


def save_config(config: Config, path: str) -> None:
    """
    将配置写入 YAML 文件

    Args:
        config: 待保存的 Config 对象
        path: 目标文件路径

    Note:
        全程持有与目标文件同名的进程间锁，并通过 atomic_write 原子替换，
        避免 CLI 与 MCP Server 同时保存配置时互相覆盖或写出被截断的文件

    Example:
        >>> save_config(config, "jenkins-config.yaml")  # doctest: +SKIP
    """
    import yaml

    data = config_to_dict(config)
    target = Path(path)

    def _write(f) -> None:
        f.write(TEMPLATE_HEADER)
        yaml.dump(
            data,
            f,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
            indent=2,
            width=80,
        )

    with file_lock(target, required=True):
        atomic_write(target, _write)


def config_to_dict(config: Config) -> dict[str, Any]:
    """将 Config 对象序列化为字典"""
    environments = {}
    for env_name, env in config.environments.items():
        env_dict = {}
        if env.description:
            env_dict["description"] = env.description
        if env.branch_field:
            env_dict["branch_field"] = env.branch_field
        if env.params:
            env_dict["params"] = dict(env.params)
        if env.projects:
            env_dict["projects"] = [
                _project_to_dict(p) for p in env.projects
            ]
        environments[env_name] = env_dict

    result = {
        "server": asdict(config.server),
        "build": asdict(config.build),
    }

    if config.branch_field != "branch":
        result["branch_field"] = config.branch_field

    if environments:
        result["environments"] = environments

    return result


def _project_to_dict(p: Project) -> dict[str, Any]:
    """将 Project 转为字典（移除 None 值）"""
    d: dict[str, Any] = {"name": p.name}
    if p.path and p.path != p.name:
        d["path"] = p.path
    if p.params:
        d["params"] = dict(p.params)
    return d


# ============================================================================
# 模板
# ============================================================================


def generate_template() -> dict[str, Any]:
    """生成最小配置文件模板字典

    server.url / server.token 取自 PLACEHOLDER_VALUES，与"配置是否填过"的
    判据同源（见该常量注释）。

    Returns:
        可直接序列化为 YAML/JSON 的模板字典

    Example:
        >>> generate_template()["server"]["url"] == PLACEHOLDER_VALUES["server.url"]
        True
    """
    server = ServerConfig(
        url=PLACEHOLDER_VALUES["server.url"],
        token=PLACEHOLDER_VALUES["server.token"],
    )
    return {
        "server": asdict(server),
        "build": asdict(BuildConfig()),
        "branch_field": "branch",
        "environments": {
            "dev": {
                "description": "开发环境",
                "params": {"branch": "develop"},
                "projects": [{"name": "project-a"}],
            },
            "prod": {
                "description": "生产环境",
                "params": {"branch": "main"},
                "projects": [{"name": "project-a-prod"}],
            },
        },
    }


def template_fields() -> list[dict[str, Any]]:
    """返回模板字段说明清单（供 MCP init_config 回传给客户端）

    文案与 CLI 的 show_template() 同源（都渲染 TEMPLATE_FIELD_SPECS），
    调用方拿到 required=True 的键就知道该催用户去填哪几项。

    Returns:
        字典列表，每项含 key（点分键名）、description（说明）、required（是否必填）

    Example:
        >>> [item["key"] for item in template_fields() if item["required"]][:2]
        ['server', 'server.url']
    """
    return [
        {"key": key, "description": description, "required": required}
        for key, description, required in TEMPLATE_FIELD_SPECS
    ]


def template_text(fmt: str = "yaml") -> str:
    """把配置模板序列化为可直接落盘的文本

    模板内容取自 generate_template()（纯 dict 常量），**不读源码树里的
    jenkins-config.example.yaml**：npx 与单文件 EXE 形态下没有源码树，
    读示例文件会在真实部署场景直接 FileNotFoundError。

    Args:
        fmt: 序列化格式，取 'yaml'（带头部注释）或 'json'（无注释）

    Returns:
        可写入文件的完整文本，以换行结尾

    Raises:
        ValueError: fmt 不是 yaml / json

    Example:
        >>> template_text("json").lstrip().startswith("{")
        True
    """
    normalized = fmt.strip().lower()
    if normalized not in ("yaml", "json"):
        raise ValueError(f"不支持的模板格式: {fmt}（仅支持 yaml / json）")

    data = generate_template()
    if normalized == "json":
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    import yaml

    required = [key for key, _, flag in TEMPLATE_FIELD_SPECS if flag]
    header = (
        f"{TEMPLATE_HEADER}"
        f"# 必填项: {'、'.join(required)}\n"
        f"# server.url / server.token 当前仍是占位符，必须改为真实取值\n\n"
    )
    return header + yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        indent=2,
        width=80,
    )


def show_template() -> None:
    """打印配置文件模板说明（CLI --show-template）

    字段清单从 TEMPLATE_FIELD_SPECS 渲染，与 template_fields() 同源。

    Example:
        >>> show_template()  # doctest: +SKIP
    """
    lines = [
        "=" * 64,
        "  Jenkins 配置文件模板 (jenkins-config.yaml)",
        "=" * 64,
        "",
    ]
    width = max(len(key) for key, _, _ in TEMPLATE_FIELD_SPECS)
    for key, description, required in TEMPLATE_FIELD_SPECS:
        mark = "必填" if required else "可选"
        lines.append(f"  {key:<{width}}  {mark}  {description}")
    lines += [
        "",
        "-" * 64,
        "  参数合并: 项目 params > 环境 params",
        "  分支覆写: CLI -b 会覆盖 params 中 branch_field 指定的键",
        "  新增插件: 直接在 params 中添加键值对，无需修改代码",
        "-" * 64,
    ]
    print("\n".join(lines))
