#!/bin/bash
# jenkins-auto-build.sh - Python CLI 包装器
# ============================================================================
# Jenkins 自动构建脚本入口
# ============================================================================

if command -v dirname &> /dev/null; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$(pwd)"
fi
cd "$SCRIPT_DIR"

# 优先使用 venv 中的 python（跳过 uv run 的 ~0.6s 开销）
# 仅在 venv 不存在时回退到 uv run
if [ -f ".venv/Scripts/python.exe" ]; then
    exec .venv/Scripts/python.exe -m jenkins_config.cli "$@"
elif [ -f ".venv/bin/python" ]; then
    exec .venv/bin/python -m jenkins_config.cli "$@"
else
    echo "[INFO] 正在准备环境..." >&2
    exec uv run python -m jenkins_config.cli "$@"
fi