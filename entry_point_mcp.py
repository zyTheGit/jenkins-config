#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
MCP Server 独立入口脚本 - 用于 PyInstaller 打包

与 entry_point.py（CLI 入口）并列，同样避免相对导入，
打出的二进制自带 Python 运行时，使用方无需安装 Python。
"""

import sys
from pathlib import Path

# 将项目根目录添加到 Python 路径
# 这样可以正确导入 jenkins_config 包
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 导入并运行 MCP Server 主函数
from jenkins_config.mcp.server import main

if __name__ == '__main__':
    main()
