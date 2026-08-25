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

带连字符的 tag（如 `v1.6.0-rc.1`）会发布为 pre-release。

## npm 自动发布

`release` job 完成后，`publish-npm` job 会自动把 `npm/` 发到 npmjs.com：

- 版本号从 tag 推导（`v1.6.0` → `1.6.0`），`npm/package.json` 不需要手工改，也不会再和 Release 资产版本漂移
- 预发布 tag 发到 `next` 频道，不抢占 `latest`；正式 tag 发到 `latest`
- 带 `--provenance`，在 npm 页面上可追溯到具体的 workflow run

前置条件：仓库 Settings → Secrets and variables → Actions 里添加 `NPM_TOKEN`，类型选 npm 的 **Automation** token（开了 2FA 的账号用普通 token 会被拒）。缺少该 secret 时 `publish-npm` 会直接报错退出，不影响已经发好的 GitHub Release。

发布顺序是有意为之：先传 Release 资产，再发 npm。反过来的话用户装到包却下不到二进制。

