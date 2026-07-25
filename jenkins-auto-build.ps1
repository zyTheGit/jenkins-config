#!/usr/bin/env pwsh
# jenkins-auto-build.ps1 - Python CLI 包装器 (PowerShell)
# ============================================================================
# Jenkins 自动构建脚本入口 (Windows)
# 使用方式: .\jenkins-auto-build.ps1 --help
# ============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# 优先使用 venv 中的 python（跳过 uv run 的 ~0.6s 开销）
# 仅在 venv 不存在时回退到 uv run
if (Test-Path ".venv\Scripts\python.exe") {
    & .venv\Scripts\python.exe -m jenkins_config.cli @args
} elseif (Test-Path ".venv\bin\python") {
    & .venv\bin\python -m jenkins_config.cli @args
} else {
    Write-Host "[INFO] 正在准备环境..." -ForegroundColor Yellow
    uv run python -m jenkins_config.cli @args
}
