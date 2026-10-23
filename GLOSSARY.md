# Glossary

本项目的领域术语。实现代码时应优先使用这里的名称。

## Build flow

一次构建请求从「选哪些 Job」到「触发并记录」的完整规则集合，实现位于
`jenkins_config/build_flow.py`。CLI 与 MCP Server 只是它的适配器：确认交互、
写入门禁、日志渲染和返回文案属于适配器，构建流程规则只实现一次。

## Job selection

把环境、项目过滤、分支覆写与额外参数合并成待构建 Job 列表的过程。
环境级参数先于项目级参数，参数覆写最后生效；无法作为 Jenkins 参数传递的
非标量值会被跳过并单独返回。

## Blocking build

触发后轮询到终态再返回的构建方式，供 CLI 使用。结果包含每个 Job 的状态、
耗时与日志路径；同一批构建共用一个历史时间戳，失败结果也会写入历史。

## Queue trigger

触发后立即返回、最多用一小段预算探测构建编号的方式，供 MCP Server 使用。
写入历史的是 `BUILDING` 占位记录，编号未分配时 `build_num=0`，不参与重建分组。

## Build record

构建历史中的一条持久化记录（`BuildRecord`），包含时间戳、环境、Job 标识、
构建编号、状态、分支、参数与日志路径。同一次构建请求写入的记录共享时间戳，
`get_last_build_group` 以此分组。

## Failure payload

MCP 侧一切失败的统一返回体：`error_code` / `error` / `config_path` / `next_steps` / `docs`
五个字段。错误码是闭集，分类维度是"用户下一步该做什么"；`next_steps` 恒不为空且每条
都是可直接执行的动作。分类与构造都由 `jenkins_config/mcp/errors.py` 承担，Tool 与
Resource 共用同一形状。

## Unusable config（配置不可用）

`CONFIG_UNUSABLE_CODES` 这一组错误码（文件不存在、语法坏、路径越界、无读权限、家目录
不可用）表示"配置本身立不起来"，任何依赖配置的读操作都必须在此之前短路。
`config_incomplete`（凭据仍是占位符）**不属于**这一组：它只影响访问 Jenkins，
读本地构建历史照样成立。

## Config priority order（配置优先级顺序）

候选配置文件路径的全序：先按候选目录顺序（源码模式为项目根 → CWD → 用户级配置目录，
EXE 模式为 CWD → exe 目录 → 用户级配置目录，每个目录先取它的 `.jenkins-config`
子目录再取目录本身），同一目录内按 `CONFIG_FILE_NAMES`（yaml → yml → json）。
这份顺序只写在 `paths.config_candidates()`：探测取第一份存在的是它，
判定"哪份压过哪份"（`paths.config_rank()`，`init_config` 的遮蔽检查用它）也是它。

## History anchoring（历史文件锚定）

构建历史文件的位置规则：`<配置文件所在目录>/data/build_history.json`，
只写在 `paths.resolve_history_path()`，且不要求配置文件真的存在。
MCP 侧不直接用它，而用 `mcp/utils.history_path_for()`：先按白名单完成寻址再锚定。
两个名字曾各自叫 `resolve_history_path` 而契约相反（一个收调用方原始参数、
一个先校验），这是走错步的地方，故 MCP 侧入口改名。
