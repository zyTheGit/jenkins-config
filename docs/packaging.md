# 打包

CI 已在 `v*` tag 上自动打好全平台二进制并发到 GitHub Release，通常无需本地打包。以下是本地构建方式。

## 安装打包工具

```bash
uv pip install pyinstaller
```

## 打包命令

```bash
# 单文件模式（默认，便于分发，约 15 MB）
uv run python build.py

# 目录模式（启动更快，文件较多）
uv run python build.py --dir

# 自定义 exe 图标
uv run python build.py --icon assets/my-icon.ico

# 清理后重新打包
uv run python build.py --clean

# 打包 MCP Server 二进制（供 npx 启动器下载使用）
uv run python build.py --target mcp

# 同时打包 CLI 与 MCP Server
uv run python build.py --target all
```

## 打包输出

```
dist/
├── jenkins-build.exe       # CLI（单文件模式）
└── jenkins-config-mcp.exe  # MCP Server（--target mcp/all 时产出）

# 或目录模式
dist/
└── jenkins-build/
    └── jenkins-build.exe
```

## EXE 使用说明

1. 将 `jenkins-config.yaml` 放在 exe 同级目录
2. 或使用 `-c` 参数指定配置文件路径

```bash
# 配置文件在同级目录
jenkins-build.exe --list-envs

# 指定配置文件路径
jenkins-build.exe -c /path/to/config.yaml --list-envs
```

## CI 发布产物

`.github/workflows/build.yml` 在 `v*` tag 上构建并上传：

- `jenkins-config-mcp-{win-x64.exe,macos-x64,macos-arm64,linux-x64,linux-arm64}` — MCP Server
- `jenkins-build-{win.exe,macos,linux}` — CLI
- `checksums.txt` — 上述全部资产的 sha256，npx 启动器据此校验

带连字符的 tag（如 `v1.6.0-rc.1`）会发布为 pre-release。npm 包版本需与 tag 保持一致，否则 `npx` 默认会去下载不存在的 Release。
