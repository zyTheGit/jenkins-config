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
