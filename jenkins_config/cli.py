# jenkins_config/cli.py
"""
命令行入口模块 - 提供 CLI 命令解析和交互式界面

这个模块是整个工具的入口点，主要功能：
1. 解析命令行参数（argparse）
2. 提供交互式选择界面（questionary）
3. 协调各个模块完成构建流程
4. 输出构建报告

支持的命令：
- 构建命令：-e, -j, -m, -p 等参数
- 列表命令：--list-envs, --list-projects
- 历史命令：--history, --history-stats
- 交互模式：-i, --interactive

使用示例：
    # 显示帮助
    ./jenkins-auto-build.sh --help

    # 列出所有环境
    ./jenkins-auto-build.sh --list-envs

    # 构建指定环境
    ./jenkins-auto-build.sh -e dev

    # 构建指定项目
    ./jenkins-auto-build.sh -j dev:project-a,test:project-b

    # 交互式选择
    ./jenkins-auto-build.sh -i
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# questionary 库提供交互式终端界面
import questionary
from questionary import Style

# 导入项目内部模块
from .config import Config
from .jenkins import JenkinsClient
from .builder import Builder, BuildResult
from .history import HistoryManager, BuildRecord
from .jenkins import BuildStatus
from .utils import (
    log_info, log_success, log_error, log_warn,
    print_sep, print_header, format_duration
)


# ============================================================================
# 交互式界面样式配置
# ============================================================================

# 自定义 questionary 样式，控制颜色和显示效果
CUSTOM_STYLE = Style([
    ('qmark', 'fg:cyan bold'),        # 问号标记：青色加粗
    ('question', 'fg:white bold'),    # 问题文本：白色加粗
    ('answer', 'fg:green bold'),      # 答案：绿色加粗
    ('pointer', 'fg:cyan bold'),      # 选择指针：青色加粗
    ('highlighted', 'fg:cyan bold'),  # 高亮选项：青色加粗
    ('selected', 'fg:green'),         # 已选中：绿色
    ('separator', 'fg:gray'),         # 分隔符：灰色
    ('instruction', 'fg:gray'),       # 操作提示：灰色
    ('text', 'fg:white'),             # 普通文本：白色
])


# ============================================================================
# 主入口函数
# ============================================================================

def main():
    """
    CLI 主入口函数
    
    解析命令行参数，根据参数调用相应的功能函数。
    这是整个工具的入口点，在 pyproject.toml 中配置为：
    [project.scripts]
    jenkins-build = "jenkins_config.cli:main"
    """
    # ------------------------------------------------------------------------
    # 创建参数解析器
    # ------------------------------------------------------------------------
    parser = argparse.ArgumentParser(
        description="Jenkins 自动构建工具",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # ------------------------------------------------------------------------
    # 构建参数
    # ------------------------------------------------------------------------
    parser.add_argument(
        "-m", "--mode",
        choices=["parallel", "sequential"],
        default="parallel",
        help="构建模式：parallel（并行）或 sequential（顺序）"
    )
    parser.add_argument(
        "-e", "--env",
        help="构建指定环境的所有项目"
    )
    parser.add_argument(
        "-j", "--jobs",
        help="构建指定项目，格式: env:project,env:project"
    )
    parser.add_argument(
        "-p", "--params",
        help="额外构建参数，格式: key=value&key2=value2"
    )
    parser.add_argument(
        "-c", "--config",
        default="jenkins-config.json",
        help="配置文件路径（默认: jenkins-config.json）"
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="交互式选择要构建的项目"
    )
    
    # ------------------------------------------------------------------------
    # 列表命令
    # ------------------------------------------------------------------------
    parser.add_argument(
        "--list-envs",
        action="store_true",
        help="列出所有环境"
    )
    parser.add_argument(
        "--list-projects",
        metavar="ENV",
        nargs="?",
        const="",  # 不带参数时使用空字符串
        help="列出项目，不指定 ENV 则列出所有"
    )
    
    # ------------------------------------------------------------------------
    # 历史命令
    # ------------------------------------------------------------------------
    parser.add_argument(
        "--history",
        action="store_true",
        help="查看构建历史"
    )
    parser.add_argument(
        "--history-stats",
        action="store_true",
        help="查看历史统计"
    )
    
    # ------------------------------------------------------------------------
    # 其他选项
    # ------------------------------------------------------------------------
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="调试模式"
    )
    
    # 解析命令行参数
    args = parser.parse_args()
    
    # ------------------------------------------------------------------------
    # 确定配置文件路径
    # 
    # 在不同运行环境下处理配置文件路径：
    # 1. 源码运行：相对于项目根目录
    # 2. exe 运行：相对于 exe 所在目录或当前工作目录
    # ------------------------------------------------------------------------
    config_file = Path(args.config)
    
    if not config_file.is_absolute():
        # 检查是否在 PyInstaller 打包环境中运行
        # getattr(sys, 'frozen', False) 在 exe 模式下返回 True
        if getattr(sys, 'frozen', False):
            # exe 模式：优先使用当前工作目录，然后是 exe 所在目录
            exe_dir = Path(sys.executable).parent
            
            # 1. 先检查当前工作目录
            cwd_config = Path.cwd() / args.config
            if cwd_config.exists():
                config_file = cwd_config
            # 2. 再检查 exe 所在目录
            else:
                exe_config = exe_dir / args.config
                if exe_config.exists():
                    config_file = exe_config
                else:
                    # 都不存在，默认使用当前工作目录（会在后续报错）
                    config_file = cwd_config
        else:
            # 源码模式：相对于项目根目录
            script_dir = Path(__file__).parent.parent
            config_file = script_dir / args.config
    
    # ------------------------------------------------------------------------
    # 根据参数调用相应的功能
    # ------------------------------------------------------------------------
    
    # 列表命令
    if args.list_envs:
        list_environments(config_file)
        return
    
    if args.list_projects is not None:
        list_projects(config_file, args.list_projects)
        return
    
    # 历史命令
    if args.history:
        show_history(config_file, args.env)
        return
    
    if args.history_stats:
        show_history_stats(config_file)
        return
    
    # 交互式选择模式
    if args.interactive:
        run_interactive_build(config_file, args)
        return
    
    # 默认：执行构建流程
    run_build(config_file, args)


# ============================================================================
# 列表命令函数
# ============================================================================

def list_environments(config_file: Path):
    """
    列出所有环境
    
    从配置文件读取并显示所有可用的环境名称。
    
    Args:
        config_file: 配置文件路径
    """
    config = Config.load(str(config_file))
    print_header("所有环境")
    for env in config.list_environments():
        print(f"  - {env}")


def list_projects(config_file: Path, env: str | None):
    """
    列出项目
    
    显示指定环境或所有环境的项目列表。
    
    Args:
        config_file: 配置文件路径
        env: 环境名称，为空字符串时列出所有环境的项目
    """
    config = Config.load(str(config_file))
    
    if env:
        # 显示指定环境的项目
        print_header(f"环境 '{env}' 的项目")
        for e, name, path in config.list_projects(env):
            print(f"  - {name} ({path})")
    else:
        # 显示所有环境的项目（按环境分组）
        print_header("所有环境的项目")
        current_env = None
        for e, name, path in config.list_projects():
            # 环境变化时打印环境标题
            if e != current_env:
                print(f"\n[{e}]")
                current_env = e
            print(f"  - {name} ({path})")


# ============================================================================
# 历史命令函数
# ============================================================================

def show_history(config_file: Path, env: str | None):
    """
    显示构建历史
    
    查询并显示最近的构建记录。
    
    Args:
        config_file: 配置文件路径
        env: 按环境过滤，为 None 时显示所有
    """
    # 历史文件位于配置文件同级目录的 data 子目录
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    records = manager.list(env=env, limit=20)
    
    print_header("构建历史")
    if not records:
        print("暂无记录")
        return
    
    for r in records:
        # 成功显示 ✓，失败显示 ✗
        status_icon = "✓" if r.status == "SUCCESS" else "✗"
        print(f"  {status_icon} [{r.timestamp}] {r.job_key} #{r.build_num} - {r.status} ({format_duration(r.duration)})")


def show_history_stats(config_file: Path):
    """
    显示历史统计
    
    显示总构建数、成功数、失败数和成功率。
    
    Args:
        config_file: 配置文件路径
    """
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    stats = manager.stats()
    
    print_header("历史统计")
    print(f"  总构建数: {stats['total']}")
    print(f"  成功数: {stats['success']}")
    print(f"  失败数: {stats['failure']}")
    print(f"  成功率: {stats['success_rate']}%")


# ============================================================================
# 交互式构建函数
# ============================================================================

def run_interactive_build(config_file: Path, args):
    """
    交互式构建选择
    
    通过交互式界面让用户选择：
    1. 构建方式（按环境或按项目）
    2. 要构建的环境/项目
    3. 构建模式（并行/顺序）
    4. 确认后执行构建
    
    Args:
        config_file: 配置文件路径
        args: 命令行参数对象
    """
    print_header("交互式构建选择")
    
    # 加载配置
    try:
        config = Config.load(str(config_file))
    except FileNotFoundError as e:
        log_error(str(e))
        sys.exit(1)
    
    # ========================================================================
    # 步骤 1：选择构建方式
    # ========================================================================
    build_method = questionary.select(
        "请选择构建方式:",
        choices=[
            questionary.Choice(
                title="按环境构建 - 选择一个环境，然后选择项目",
                value="by_env"
            ),
            questionary.Choice(
                title="按项目构建 - 从所有项目中多选",
                value="by_project"
            ),
        ],
        style=CUSTOM_STYLE
    ).ask()
    
    # 用户取消选择
    if not build_method:
        log_warn("已取消")
        sys.exit(0)
    
    selected_env = None
    jobs_filter = None
    
    # ========================================================================
    # 步骤 2：根据构建方式进行选择
    # ========================================================================
    
    if build_method == "by_env":
        # --------------------------------------------------------------------
        # 按环境构建：先选环境，再选项目
        # --------------------------------------------------------------------
        envs = config.list_environments()
        if not envs:
            log_error("没有可用的环境")
            sys.exit(1)
        
        # 选择环境
        selected_env = questionary.select(
            "请选择要构建的环境:",
            choices=envs,
            style=CUSTOM_STYLE
        ).ask()
        
        if not selected_env:
            log_warn("已取消")
            sys.exit(0)
        
        # 获取该环境的项目列表
        projects = config.list_projects(selected_env)
        if not projects:
            log_error(f"环境 '{selected_env}' 没有可用的项目")
            sys.exit(1)
        
        # 构建项目选项
        project_choices = [
            questionary.Choice(
                title=f"{name} ({path})",
                value=f"{selected_env}:{name}"
            )
            for _, name, path in projects
        ]
        
        # 添加"全选"选项
        all_choice = questionary.Choice(
            title="【全选】构建该环境所有项目",
            value="__ALL__"
        )
        project_choices.insert(0, all_choice)
        
        # 多选项目
        selected_projects = questionary.checkbox(
            "请选择要构建的项目 (空格选择，回车确认):",
            choices=project_choices,
            style=CUSTOM_STYLE
        ).ask()
        
        if not selected_projects:
            log_warn("已取消")
            sys.exit(0)
        
        # 处理全选
        if "__ALL__" in selected_projects:
            jobs_filter = None  # 不过滤，获取该环境所有项目
        else:
            jobs_filter = selected_projects
    
    else:
        # --------------------------------------------------------------------
        # 按项目构建：从所有项目中选择（跨环境）
        # --------------------------------------------------------------------
        all_projects = config.list_projects()
        if not all_projects:
            log_error("没有可用的项目")
            sys.exit(1)
        
        # 按环境分组显示项目
        project_choices = []
        current_env = None
        
        for env, name, path in all_projects:
            # 添加环境分隔符
            if env != current_env:
                project_choices.append(
                    questionary.Choice(
                        title=f"─── [{env}] ───",
                        disabled=True,  # 不可选
                        value=f"separator_{env}"
                    )
                )
                current_env = env
            
            # 添加项目选项
            project_choices.append(
                questionary.Choice(
                    title=f"  {name} ({path})",
                    value=f"{env}:{name}"
                )
            )
        
        # 多选项目
        selected_projects = questionary.checkbox(
            "请选择要构建的项目 (空格选择，回车确认):",
            choices=project_choices,
            style=CUSTOM_STYLE
        ).ask()
        
        if not selected_projects:
            log_warn("已取消")
            sys.exit(0)
        
        jobs_filter = selected_projects
    
    # ========================================================================
    # 步骤 3：选择构建模式
    # ========================================================================
    build_mode = questionary.select(
        "请选择构建模式:",
        choices=[
            questionary.Choice(
                title="并行构建 (同时构建所有项目)",
                value="parallel"
            ),
            questionary.Choice(
                title="顺序构建 (按顺序逐个构建)",
                value="sequential"
            ),
        ],
        style=CUSTOM_STYLE
    ).ask()
    
    if not build_mode:
        log_warn("已取消")
        sys.exit(0)
    
    # ========================================================================
    # 步骤 4：获取要构建的 jobs
    # ========================================================================
    if build_method == "by_env" and jobs_filter is None:
        # 按环境构建且全选
        jobs = config.get_jobs(env=selected_env)
    elif build_method == "by_env":
        # 按环境构建且有筛选
        jobs = config.get_jobs(env=selected_env, jobs=jobs_filter)
    else:
        # 按项目构建
        jobs = config.get_jobs(jobs=jobs_filter)
    
    if not jobs:
        log_error("没有找到匹配的项目")
        sys.exit(1)
    
    # ========================================================================
    # 步骤 5：确认构建
    # ========================================================================
    print()
    print_sep("-")
    print(f"即将构建以下 {len(jobs)} 个项目:")
    print_sep("-")
    for job in jobs:
        print(f"  - [{job.env}] {job.key} ({job.path})")
    print_sep("-")
    
    confirm = questionary.confirm(
        "确认开始构建?",
        default=True,
        style=CUSTOM_STYLE
    ).ask()
    
    if not confirm:
        log_warn("已取消")
        sys.exit(0)
    
    # ========================================================================
    # 步骤 6：执行构建
    # ========================================================================
    print()
    args.env = selected_env
    args.mode = build_mode
    args.jobs = ",".join(jobs_filter) if jobs_filter else None
    
    # 调用构建流程
    run_build(config_file, args)


# ============================================================================
# 构建执行函数
# ============================================================================

def run_build(config_file: Path, args):
    """
    执行构建流程
    
    这是构建的核心流程：
    1. 加载配置
    2. 获取要构建的 jobs
    3. 创建 Jenkins 客户端和构建器
    4. 执行构建（并行或顺序）
    5. 保存历史记录
    6. 生成报告
    
    Args:
        config_file: 配置文件路径
        args: 命令行参数对象
    """
    print_header("Jenkins 自动构建脚本")
    print(f"构建模式: {args.mode}")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # ------------------------------------------------------------------------
    # 加载配置
    # ------------------------------------------------------------------------
    try:
        config = Config.load(str(config_file))
    except FileNotFoundError as e:
        log_error(str(e))
        sys.exit(1)
    
    # ------------------------------------------------------------------------
    # 获取要构建的 jobs
    # ------------------------------------------------------------------------
    # 解析 -j 参数
    jobs_filter = args.jobs.split(",") if args.jobs else None
    jobs = config.get_jobs(env=args.env, jobs=jobs_filter)
    
    if not jobs:
        log_error("没有找到匹配的项目")
        sys.exit(1)
    
    # 显示将要构建的项目
    print_sep("-")
    print("将要构建的 Job:")
    print_sep("-")
    for job in jobs:
        print(f"  - {job.key}: {job.path}")
    print_sep("-")
    print()
    
    # ------------------------------------------------------------------------
    # 创建日志目录
    # 按日期创建子目录，如 jenkins_logs/build_20260320/
    # ------------------------------------------------------------------------
    log_dir = Path(config.build.log_dir) / f"build_{datetime.now().strftime('%Y%m%d')}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"日志目录: {log_dir}")
    
    # ------------------------------------------------------------------------
    # 覆盖参数
    # -p 参数指定的参数会覆盖配置文件中的参数
    # ------------------------------------------------------------------------
    if args.params:
        override_params = Config._parse_params(args.params)
        for job in jobs:
            job.params.update(override_params)
    
    # ------------------------------------------------------------------------
    # 创建客户端和构建器
    # ------------------------------------------------------------------------
    client = JenkinsClient(
        url=config.server.url,
        token=config.server.token,
        timeout=config.build.curl_timeout
    )
    builder = Builder(client, config)
    
    # ------------------------------------------------------------------------
    # 执行构建
    # ------------------------------------------------------------------------
    if args.mode == "parallel":
        results = builder.build_parallel(jobs, str(log_dir))
    else:
        results = builder.build_sequential(jobs, str(log_dir))
    
    # ------------------------------------------------------------------------
    # 保存历史记录
    # ------------------------------------------------------------------------
    history_file = config_file.parent / "data" / "build_history.json"
    manager = HistoryManager(str(history_file))
    
    for result in results:
        # 查找对应的 job 以获取环境信息
        job = next((j for j in jobs if j.key == result.job_key), None)
        manager.add(BuildRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            env=job.env if job else "",
            job_key=result.job_key,
            build_num=result.build_num,
            status=result.status.value,
            duration=result.duration,
            log_file=result.log_file
        ))
    
    # ------------------------------------------------------------------------
    # 生成报告
    # ------------------------------------------------------------------------
    generate_report(results, str(log_dir))


# ============================================================================
# 报告生成函数
# ============================================================================

def generate_report(results: list[BuildResult], log_dir: str):
    """
    生成构建结果报告
    
    显示构建统计和详细结果。
    
    Args:
        results: 构建结果列表
        log_dir: 日志目录路径
    """
    print_header("构建结果汇总")
    
    # 统计
    total = len(results)
    success = sum(1 for r in results if r.status == BuildStatus.SUCCESS)
    failure = total - success
    
    print(f"总计: {total} 个 Job")
    print(f"成功: {success} 个")
    print(f"失败: {failure} 个")
    print()
    
    # 详细结果
    print_sep("-")
    print("详细结果:")
    print_sep("-")
    
    for result in results:
        # 根据状态显示不同的图标和颜色
        if result.status == BuildStatus.SUCCESS:
            print(f"  ✓ {result.job_key}: SUCCESS (#{result.build_num})")
        elif result.status == BuildStatus.FAILURE:
            print(f"  ✗ {result.job_key}: FAILURE (#{result.build_num})")
        elif result.status == BuildStatus.ABORTED:
            print(f"  ! {result.job_key}: ABORTED (#{result.build_num})")
        elif result.status == BuildStatus.TIMEOUT:
            print(f"  ✗ {result.job_key}: TIMEOUT (#{result.build_num})")
        else:
            print(f"  ? {result.job_key}: {result.status.value} (#{result.build_num})")
    
    print_sep("-")
    print(f"日志目录: {log_dir}")
    print_sep("=")
    
    # 如果有失败的，返回非零退出码
    if failure > 0:
        sys.exit(1)


# ============================================================================
# 入口点
# ============================================================================

if __name__ == "__main__":
    main()