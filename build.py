#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包脚本 - 将项目打包成 exe

使用方法：
    python build.py          # 打包成 exe
    python build.py --clean  # 清理后重新打包
    python build.py --dir    # 使用目录模式（启动更快）
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def clean_build():
    """清理构建目录"""
    print("正在清理构建目录...")
    
    dirs_to_clean = ['build', 'dist', '__pycache__']
    for dir_name in dirs_to_clean:
        if Path(dir_name).exists():
            shutil.rmtree(dir_name)
            print(f"  已删除: {dir_name}/")
    
    # 删除 .spec 文件生成的临时文件
    for spec_file in Path('.').glob('*.spec'):
        if spec_file.name != 'jenkins-build.spec':
            spec_file.unlink()
            print(f"  已删除: {spec_file}")
    
    print("清理完成！\n")


def build_exe(mode='onefile'):
    """
    打包成 exe
    
    Args:
        mode: 'onefile' 单文件模式，'dir' 目录模式
    """
    print(f"开始打包 ({mode} 模式)...\n")
    
    # 检查 PyInstaller 是否安装
    try:
        import PyInstaller
        print(f"PyInstaller 版本: {PyInstaller.__version__}")
    except ImportError:
        print("正在安装 PyInstaller...")
        subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'], check=True)
    
    # 构建 PyInstaller 命令
    if mode == 'onefile':
        # 单文件模式
        cmd = [
            sys.executable, '-m', 'PyInstaller',
            '--onefile',           # 打包成单个文件
            '--console',           # 控制台程序
            '--name', 'jenkins-build',  # 输出文件名
            '--clean',             # 清理临时文件
            # 隐藏导入
            '--hidden-import', 'requests',
            '--hidden-import', 'questionary',
            '--hidden-import', 'prompt_toolkit',
            '--hidden-import', 'prompt_toolkit.input',
            '--hidden-import', 'prompt_toolkit.output',
            '--hidden-import', 'prompt_toolkit.styles',
            '--hidden-import', 'wcwidth',
            # 排除不需要的模块
            '--exclude-module', 'tkinter',
            '--exclude-module', 'matplotlib',
            '--exclude-module', 'numpy',
            '--exclude-module', 'pandas',
            '--exclude-module', 'PIL',
            # 入口文件
            'entry_point.py',
        ]
    else:
        # 目录模式
        cmd = [
            sys.executable, '-m', 'PyInstaller',
            '--onedir',            # 打包成目录
            '--console',           # 控制台程序
            '--name', 'jenkins-build',  # 输出目录名
            '--clean',             # 清理临时文件
            '--hidden-import', 'requests',
            '--hidden-import', 'questionary',
            '--hidden-import', 'prompt_toolkit',
            '--hidden-import', 'prompt_toolkit.input',
            '--hidden-import', 'prompt_toolkit.output',
            '--hidden-import', 'prompt_toolkit.styles',
            '--hidden-import', 'wcwidth',
            '--exclude-module', 'tkinter',
            '--exclude-module', 'matplotlib',
            '--exclude-module', 'numpy',
            '--exclude-module', 'pandas',
            '--exclude-module', 'PIL',
            'entry_point.py',
        ]
    
    # 执行打包
    print(f"执行命令: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    
    if result.returncode == 0:
        print("\n" + "=" * 60)
        print("打包成功！")
        print("=" * 60)
        
        if mode == 'onefile':
            exe_path = Path('dist/jenkins-build.exe')
        else:
            exe_path = Path('dist/jenkins-build/jenkins-build.exe')
        
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"\n输出文件: {exe_path.absolute()}")
            print(f"文件大小: {size_mb:.2f} MB")
        
        print("\n使用方法:")
        print(f"  {exe_path} --help")
        print(f"  {exe_path} -i          # 交互式选择")
        print(f"  {exe_path} -e dev      # 构建 dev 环境")
        print(f"  {exe_path} --list-envs # 列出所有环境")
        
        print("\n注意:")
        print("  - 首次运行需要将 jenkins-config.json 放在 exe 同级目录")
        print("  - 或使用 -c 参数指定配置文件路径")
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
    
    args = parser.parse_args()
    
    # 切换到项目根目录
    project_root = Path(__file__).parent
    os.chdir(project_root)
    
    if args.clean:
        clean_build()
    
    mode = 'dir' if args.dir else 'onefile'
    build_exe(mode)


if __name__ == '__main__':
    main()