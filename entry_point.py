#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
独立入口脚本 - 用于 PyInstaller 打包

这个脚本不使用相对导入，可以直接被 PyInstaller 打包。
"""

import sys
from pathlib import Path

# 将项目根目录添加到 Python 路径
# 这样可以正确导入 jenkins_config 包
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 导入并运行主函数
from jenkins_config.cli import main

if __name__ == '__main__':
    main()