#!/bin/bash
# jenkins-auto-build.sh - Python CLI 包装器
# ============================================================================
# Jenkins 自动构建脚本入口
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 使用 uv 运行 Python 模块
exec uv run python -m jenkins_config.cli "$@"