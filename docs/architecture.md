# 项目结构与架构

## 目录结构

```
jenkins-config/
├── jenkins-auto-build.sh       # Shell 入口（Python 包装器）
├── jenkins-auto-build.ps1      # PowerShell 入口
├── pyproject.toml              # Python 项目配置
├── jenkins-config.yaml         # 配置文件（默认）
├── jenkins-config.example.yaml # 配置示例（YAML，推荐）
├── jenkins-config.example.json # 配置示例（JSON，兼容）
├── build.py                    # PyInstaller 打包脚本
├── entry_point.py              # CLI EXE 入口点
├── entry_point_mcp.py          # MCP Server EXE 入口点
├── npm/                        # npx 启动器包（下载并拉起 MCP 二进制）
│   ├── package.json
│   └── bin/jenkins-config-mcp.js
├── jenkins_config/             # Python 包
│   ├── cli.py                  # CLI 入口
│   ├── cmd_build.py            # 构建执行
│   ├── cmd_init.py             # 配置初始化
│   ├── cmd_interactive.py      # 交互式选择
│   ├── cmd_list.py             # 列表查询
│   ├── config_types.py         # 配置数据类型（纯 dataclass）
│   ├── config_io.py            # 配置 I/O（YAML/JSON 加载、保存）
│   ├── config.py               # 配置业务方法（猴子补丁）
│   ├── paths.py                # 配置/历史路径锚定（CLI 与 MCP 共用）
│   ├── filelock.py             # 跨进程文件锁 + 原子写
│   ├── builder.py              # 构建编排（并行/顺序）
│   ├── jenkins.py              # Jenkins API 客户端
│   ├── history.py              # 构建历史
│   ├── build_result.py         # 构建结果数据类
│   ├── build_errors.py         # 错误日志生成
│   ├── utils.py                # 工具函数
│   └── mcp/                    # MCP Server（需 "mcp" extra）
├── tests/                      # 测试套件
├── docs/                       # 文档
├── data/                       # 数据目录
│   └── build_history.json      # 构建历史
└── dist/                       # 打包输出
```

MCP Server 内部结构见 [MCP Server 文档](mcp/README.md)。

## 调用关系

```
┌─────────────────────────────────────────────────────────────┐
│                         cli.py                               │
│                    (命令行入口，懒加载 cmd_*)                 │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ config_types  │   │  builder.py   │   │  history.py   │
│ config_io     │   │  (构建编排)   │   │  (历史记录)   │
│ config.py     │   └───────────────┘   └───────────────┘
│ (配置加载)    │           │
└───────────────┘           ▼
                    ┌───────────────┐
                    │  jenkins.py   │
                    │ (Jenkins API) │
                    └───────────────┘
```

## 测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 运行单个测试文件
uv run pytest tests/test_config.py -v

# 带覆盖率
uv run pytest tests/ --cov=jenkins_config --cov-report=term-missing -v
```
