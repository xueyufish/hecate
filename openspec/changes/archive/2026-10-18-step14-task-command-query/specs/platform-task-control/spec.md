# Spec Delta

## ADDED Requirements

### Requirement: 按 Task 分页查询命令回执的只读 API

系统 SHALL 提供只读查询 `GET /api/tasks/{task_id}/commands?limit=&offset=`（默认 limit=20，limit 上限 100、offset ≥ 0），返回 `{items, total}`：items 为该 Task 的命令回执列表，total 为过滤后总数。排序 SHALL 为 `issued_at DESC, command_id DESC` 的稳定序。查询 SHALL 覆盖该 Task 的全部五种命令状态（`requested`/`acknowledged`/`applied`/`rejected`/`expired`），本能力不提供状态筛选。列表读取 SHALL 是纯已记录事实：`requested` 与 `acknowledged` SHALL 原样返回，SHALL NOT 呈现或改写为 `applied`；已过期的 `requested` 在列表中 SHALL 保持 `requested`；列表查询 SHALL NOT 推进任何命令状态、SHALL NOT 触发命令执行、SHALL NOT 执行单条查询的懒过期清扫。查询引用 SHALL 从可见 Task 行的 issuer_domain 与 id 构造，SHALL NOT 将 issuer 固定为 hecate。Task 可见性 SHALL 先行校验：不存在、已删除或属于其他 workspace 的 Task SHALL 统一返回请求 Task 的 404，且可见性失败时 SHALL NOT 读取命令存储。返回字段 SHALL 沿用单条回执白名单（command_id、kind、state、issued_at、issuer、task_ref、run_ref、expires_at、expected_revision），SHALL NOT 返回 `payload`、`payload_schema_ref`、`detail_ns`、`extra` 等内部内容。命令存储查询或投影失败 SHALL 返回明确的错误（固定文案，不含存储层原文），SHALL NOT 呈现为空列表。既有 `GET /api/commands/{command_id}` 的行为（含懒过期）SHALL 保持不变。

#### Scenario: 分页与稳定排序

- **WHEN** 某 Task 存在多条命令回执，客户端以 limit=1、offset 递增分页请求
- **THEN** 各页按 `issued_at DESC, command_id DESC` 拼接后恰为全集且无重复，total 在数据不变时保持一致

#### Scenario: 五种状态全部原样返回

- **WHEN** 某 Task 的命令中同时存在 requested、acknowledged、applied、rejected、expired 五种状态
- **THEN** 列表包含全部五条且 state 均为持久化的原值，requested/acknowledged 不被呈现为 applied

#### Scenario: 过期 requested 在列表中保持原状

- **WHEN** 某 requested 命令的 expires_at 已过，客户端查询列表
- **THEN** 该命令在列表中仍为 requested，列表查询未将其改写为 expired（单条查询的懒过期行为不受影响）

#### Scenario: 列表查询零状态推进

- **WHEN** 客户端对同一 Task 连续多次查询列表
- **THEN** 各命令的 state、持久化行内容与治理事件均不变，不产生新事实

#### Scenario: 不可见 Task 统一 404 且零存储读取

- **WHEN** 以不存在、已删除或属于其他 workspace 的 task_id 请求列表
- **THEN** 响应均为该请求 task_id 的 404，且命令存储未被调用

#### Scenario: 返回字段限于白名单

- **WHEN** 列表返回任一命令回执
- **THEN** 条目字段仅为单条查询白名单九字段，不含 payload、payload_schema_ref、detail_ns、extra

#### Scenario: 存储失败明确报错

- **WHEN** 命令存储查询抛出异常
- **THEN** 响应为 500 且错误文案为固定文本，不含存储层异常原文，也不返回空列表

#### Scenario: 单条查询行为不变

- **WHEN** 客户端以 `GET /api/commands/{command_id}` 读取一条已过期的 requested 命令
- **THEN** 该读取仍执行懒过期（返回 expired），与本列表查询的只读边界互不影响

#### Scenario: 使用可见 Task 的真实 issuer

- **WHEN** 可见 Task 的 issuer_domain 不是 hecate，且命令存储中存在同 id 的不同 issuer 记录
- **THEN** 列表仅返回该可见 Task 完整引用对应的命令

#### Scenario: 投影失败与存储故障同样受保护

- **WHEN** 命令记录读取成功，但白名单字段投影失败
- **THEN** 响应为固定文案的 TASK_CONTROL_ERROR，不泄露异常原文，不返回成功空列表
