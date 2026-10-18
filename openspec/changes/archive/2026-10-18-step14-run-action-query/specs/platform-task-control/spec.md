# Spec Delta — platform-task-control

## ADDED Requirements

### Requirement: 按 Run 查询动作记录的只读 API

平台 SHALL 提供 GET /api/runs/{run_id}/actions。服务端 SHALL 在台账读取之前，按验证后的 workspace 同时核实指定 Run 及其所属 Task 存在、未删除且归属一致；任一不满足 SHALL 统一返回 NOT_FOUND。平台本地台账 SHALL 使用既有派发链的完整 Run 行引用（issuer 与平台 Run ID），SHALL NOT 使用不同的引擎 session 引用或任务最新 Run 替换查询对象。

响应每动作 SHALL 仅含 action_key、execution_id、tool_call_id、tool_name、arguments_digest、side_effect_class、state、outcome、result_digest、pending_reconciliation；SHALL NOT 返回原始参数、完整结果、session 标识、claim token 或凭据。恢复状态与已记录 outcome SHALL 分列来自真实台账，不从 Task 终态推断。

查询 SHALL 不触发执行、重试、对账或状态迁移。真实查询无记录 SHALL 返回 supported=true 与空列表；绑定台账未提供可调用查询能力 SHALL 返回 supported=false 与来源；存储或结果读取失败 SHALL 返回显式 5xx，不冒充空结果。响应和日志 SHALL NOT 包含异常原文中的连接信息、SQL 参数或业务内容，日志仅记录可关联的 Run ID 与异常类型。本 API 只读取 ledger_source 声明的已绑定平台账本，不宣称读取了远端宿主账本。

#### Scenario: 多 Run 与历史记录独立

- **WHEN** 同一 Task 有多个 Run，历史 Run 的 backend_ref 与平台 Run ID 不同
- **THEN** 各查询只返回指定平台 Run 的动作，不混入最新 Run 或后端 session 引用下的其他动作

#### Scenario: Run 与所属 Task 共同隔离

- **WHEN** Run 不存在、已删除或属于另一 workspace，或父 Task 已删除/不属于该 workspace
- **THEN** 响应统一为所请求 Run 的 404，台账查询零调用，不泄露其他资源引用

#### Scenario: 状态来自真实台账而非终态推断

- **WHEN** 一个已终态 Task 的 Run 中仍有成功、领取中、失败、结果未知动作
- **THEN** 恢复状态、outcome 和待对账标记逐项保留台账事实，不因 Task 成功而改变

#### Scenario: 白名单排除敏感与内部字段

- **WHEN** 台账有真实结果内容、session 或 claim token 等内部字段
- **THEN** HTTP 响应只含白名单字段，内部值和完整结果不进入响应

#### Scenario: 真实空结果与 Stub 不支持区分

- **WHEN** 分别查询可调用但无记录的 SQL 台账和不提供查询能力的 InMemory Stub
- **THEN** 前者 supported=true，后者 supported=false 并如实标注 ledger_source，两者均不虚报其他来源已被扫描

#### Scenario: 存储故障不伪装为空且不泄漏诊断

- **WHEN** 查询异常带有连接 URL、凭据或 SQL 参数，或读取结果不能正确投影
- **THEN** 返回固定显式 5xx；不返回空成功，不把诊断原文写入响应或日志；日志可按 Run ID 和异常类型关联

#### Scenario: 查询零副作用

- **WHEN** 对已有领取动作、结果、生命周期与未决命令执行成功、拒绝或故障查询
- **THEN** 台账、outbox、Task/Run 全字段与命令回执保持不变，不推进过期命令或写入新事实
