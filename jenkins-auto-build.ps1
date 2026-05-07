#!/usr/bin/env pwsh
# jenkins-auto-build.ps1 - Python CLI 包装器 (PowerShell)
# ============================================================================
# Jenkins 自动构建脚本入口 (Windows)
# 使用方式: .\jenkins-auto-build.ps1 --help
# ============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# 使用 uv 运行 Python 模块
uv run python -m jenkins_config.cli @args
