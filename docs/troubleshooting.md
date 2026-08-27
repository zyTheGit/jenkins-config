# 故障排除

## 配置文件不存在

```
错误：配置文件不存在: jenkins-config.yaml
```

解决：使用 `--init` 快速生成配置文件模板，或使用 `-c` 参数指定已有配置路径。

```bash
# 生成配置文件模板（含 dev/test/prod 示例环境）
./jenkins-auto-build.sh --init

# 交互式引导填写服务器信息
./jenkins-auto-build.sh --init -i
```

## EXE 无法找到配置文件

EXE 模式下，配置文件查找顺序：

1. 当前工作目录
2. EXE 所在目录

## Jenkins 触发构建返回 400

Jenkins 返回 400（Bad Request）通常是因为参数值不合法：

- **Pipeline 项目**（WorkflowJob）的 `git-parameter-plugin` 不接受 `origin/` 前缀的分支值
- **FreeStyle 项目**（FreeStyleProject）需要 `origin/` 前缀

**自动处理**：`trigger_build()` 会自动检测 Job 类型，Pipeline 项目自动去掉 `origin/` 前缀。YAML 中统一写 `origin/prod` 即可，无需手动区分。

## Jenkins 连接失败

检查：

- Jenkins 服务器地址是否正确
- API Token 是否有效
- 网络是否连通

## npx 启动器相关

先用自检模式确认解析到了什么命令（只打印，不真正启动）：

```bash
JENKINS_MCP_LAUNCHER_DRYRUN=1 npx -y @zythegit/jenkins-config-mcp
```

- **`npm error 404 Not Found ... @zythegit/jenkins-config-mcp`** — npm 包尚未发布，或用了错误的包名（注意有 `@zythegit/` 前缀）
- **sha256 校验失败** — 下载被中间层篡改或 Release 资产被替换；不要用 `JENKINS_MCP_SKIP_CHECKSUM=1` 绕过，先确认来源
- **下载 404** — 目标 tag 的 Release 不存在该平台资产，用 `JENKINS_MCP_VERSION` 显式指定一个存在的 tag
- **`Error: spawn EINVAL`** — Node 18.20 / 20.12 起（CVE-2024-27980 加固）禁止在 `shell: false` 下直接 spawn `.cmd` / `.bat`，而 pip / npm 在 Windows 生成的 `jenkins-config-mcp` shim 常常就是 `.cmd`。启动器已改为遇到批处理时经 `cmd.exe /d /s /c` 转发（自检输出里的 `via_cmd` 字段可看到），仍报此错说明用的是旧版本，升级 npm 包即可
- **macOS 提示无法验证开发者** — 二进制未签名公证，`xattr -d com.apple.quarantine <缓存路径>` 后重试

其余环境变量与解析顺序见 [MCP Server 文档](mcp/README.md)。
