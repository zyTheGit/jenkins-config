#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包脚本 - 将项目打包成 exe

使用方法：
    python build.py                  # 打包 CLI（默认）
    python build.py --target mcp     # 打包 MCP Server（供 npx 启动器下载使用）
    python build.py --target all     # 依次打包 CLI 与 MCP Server
    python build.py --clean          # 清理后重新打包
    python build.py --dir            # 使用目录模式（启动更快）
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# CLI 交互式选择依赖 questionary/prompt_toolkit，MCP Server 不需要；
# MCP Server 侧的 mcp/pydantic 有大量动态导入与数据文件，交给 --collect-all 兜底。
TARGETS = {
    'cli': {
        'name': 'jenkins-build',
        'entry': 'entry_point.py',
        'hidden_imports': [
            'requests', 'questionary', 'prompt_toolkit', 'prompt_toolkit.input',
            'prompt_toolkit.output', 'prompt_toolkit.styles', 'wcwidth', 'yaml',
            'platformdirs',
        ],
        'collect_all': [],
    },
    'mcp': {
        'name': 'jenkins-config-mcp',
        'entry': 'entry_point_mcp.py',
        'hidden_imports': ['requests', 'yaml', 'platformdirs'],
        'collect_all': [
            'mcp', 'pydantic', 'pydantic_core', 'pydantic_settings',
            'jsonschema', 'jsonschema_specifications', 'anyio', 'httpx',
        ],
    },
}

EXCLUDE_MODULES = ['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL']


def _binary_name(target: str = 'cli') -> str:
    """根据平台与构建目标返回可执行文件名称

    Args:
        target: 构建目标（TARGETS 的键）

    Returns:
        可执行文件名，Windows 下带 .exe 后缀
    """
    base = TARGETS[target]['name']
    return f"{base}.exe" if sys.platform == "win32" else base



def clean_build():
    """清理构建目录"""
    print("正在清理构建目录...")

    dirs_to_clean = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_clean:
        if Path(dir_name).exists():
            shutil.rmtree(dir_name)
            print(f"  已删除: {dir_name}/")

    # 删除所有 .spec 文件，确保下次打包使用最新配置（特别是图标变更）
    for spec_file in Path('.').glob('*.spec'):
        spec_file.unlink()
        print(f"  已删除: {spec_file}")

    print("清理完成！\n")


def build_exe(mode='onefile', args=None, target='cli'):
    """
    打包成 exe

    Args:
        mode: 'onefile' 单文件模式，'dir' 目录模式
        args: 命令行参数（用于读取自定义图标）
        target: 构建目标，'cli' 或 'mcp'
    """
    spec = TARGETS[target]
    print(f"开始打包 {spec['name']} ({mode} 模式)...\n")


    # 检查 PyInstaller 是否安装
    try:
        import PyInstaller
        print(f"PyInstaller 版本: {PyInstaller.__version__}")
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'], check=True)

    # 构建 PyInstaller 命令
    icon_option = []
    icon_source = "PyInstaller 默认"
    if args and args.icon:
        icon_path = Path(args.icon)
        if icon_path.exists():
            icon_option = ['--icon', str(icon_path.absolute())]
            icon_source = str(icon_path)
            print(f"使用自定义图标: {icon_path.absolute()}")
        else:
            print(f"警告: 图标文件不存在: {icon_path}")
            # 回退使用默认图标
            default_icon = Path("assets/logo.ico")
            if default_icon.exists():
                icon_option = ['--icon', str(default_icon.absolute())]
                icon_source = str(default_icon)
                print(f"回退使用默认图标: {default_icon.absolute()}")
    else:
        default_icon = Path("assets/logo.ico")
        if default_icon.exists():
            icon_option = ['--icon', str(default_icon.absolute())]
            icon_source = str(default_icon)
            print(f"使用默认图标: {default_icon.absolute()}")

    hidden_import_options = []
    for module in spec['hidden_imports']:
        hidden_import_options += ['--hidden-import', module]

    collect_all_options = []
    for module in spec['collect_all']:
        collect_all_options += ['--collect-all', module]

    exclude_options = []
    for module in EXCLUDE_MODULES:
        exclude_options += ['--exclude-module', module]

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile' if mode == 'onefile' else '--onedir',
        '--console',           # 控制台程序
        '--name', spec['name'],
        '--clean',             # 清理临时文件
        # 自定义图标
        *icon_option,
        # 隐藏导入
        *hidden_import_options,
        # 动态导入 / 数据文件较多的包整体收集
        *collect_all_options,
        # 排除不需要的模块
        *exclude_options,
        # 入口文件
        spec['entry'],
    ]

    # 执行打包
    print(f"执行命令: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        print("\n" + "=" * 60)
        print("打包成功！")
        print("=" * 60)

        if mode == 'onefile':
            exe_path = Path(f'dist/{_binary_name(target)}')
        else:
            exe_path = Path(f"dist/{spec['name']}/{_binary_name(target)}")

        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"\n输出文件: {exe_path.absolute()}")
            print(f"文件大小: {size_mb:.2f} MB")
            print(f"图标来源: {icon_source}")

        if target == 'mcp':
            print("\n使用方法:")
            print(f"  {exe_path}   # 以 stdio 传输启动 MCP Server（由 MCP 客户端拉起）")

            print("\n注意:")
            print("  - 该二进制自带 Python 运行时，目标机器无需安装 Python")
            print("  - npm 包 jenkins-config-mcp 会从 GitHub Release 下载同名二进制")
        else:
            print("\n使用方法:")
            print(f"  {exe_path} --help")
            print(f"  {exe_path} -i          # 交互式选择")
            print(f"  {exe_path} -e dev      # 构建 dev 环境")
            print(f"  {exe_path} --list-envs # 列出所有环境")

            print("\n注意:")
            print("  - 首次运行需要将 jenkins-config.yaml 放在 exe 同级目录")
            print("  - 也可使用 -c 参数指定配置文件路径（支持 .yaml / .json）")
    else:
        print("\n打包失败！请检查错误信息。")
        sys.exit(1)



def main():
    parser = argparse.ArgumentParser(description='Jenkins 构建工具打包脚本')
    parser.add_argument(
        '--clean',
        action='store_true',
        help='清理构建目录后重新打包'
    )
    parser.add_argument(
        '--dir',
        action='store_true',
        help='使用目录模式（启动更快，但文件较多）'
    )
    parser.add_argument(
        '--icon',
        help='自定义 exe 图标路径（.ico 文件），默认使用 assets/logo.ico'
    )
    parser.add_argument(
        '--target',
        choices=['cli', 'mcp', 'all'],
        default='cli',
        help='构建目标：cli（默认，jenkins-build）、mcp（jenkins-config-mcp）或 all'
    )

    args = parser.parse_args()

    # 切换到项目根目录
    project_root = Path(__file__).parent
    os.chdir(project_root)

    if args.clean:
        clean_build()

    mode = 'dir' if args.dir else 'onefile'
    targets = ['cli', 'mcp'] if args.target == 'all' else [args.target]
    for target in targets:
        build_exe(mode, args, target)



if __name__ == '__main__':
    main()
