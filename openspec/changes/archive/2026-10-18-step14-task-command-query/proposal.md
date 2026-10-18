# Proposal

## Why

控制命令回执目前只有两个只读出口：单条 `GET /api/commands/{command_id}`（带懒过期副作用）和 reconciliation 的 `requested` 专项清扫——没有任何入口能按 Task 完整、分页地查看"该任务发过哪些命令、各自处于 requested/acknowledged/applied/rejected/expired"。后续工作台要展示"命令已请求、已确认、已应用"，缺的就是这个只读查询地基。命令存储（`ControlCommandRecorder` + `CommandRow`）已具备全部所需字段（workspace、task_issuer/task_id、issued_at、state），本变更只加查询能力，不建新表。

## What Changes

- 新增只读查询入口 `GET /api/tasks/{task_id}/commands?limit=&offset=`，返回 `{items, total}`，按 `issued_at DESC, command_id DESC` 稳定排序；本次不加状态筛选。
- 查询覆盖该 Task 全部五种命令状态；返回的是持久化回执原文——`requested`/`acknowledged` 不得呈现为 `applied`，列表不推进任何状态、不触发执行、不做懒过期清扫（单条查询与 reconciliation 的既有行为不变，并在 spec 中明确列表读取的是已记录状态）。
- 先校验 Task 对当前 workspace 可见（不存在/已删除/跨 workspace 统一 404，可见性失败零存储读取）；存储查询同时限定 workspace 与完整 Task 引用（issuer_domain + id），不得只匹配 task ID。
- 扩展既有 `ControlCommandRecorder` 接缝：新增按 Task 的列表查询方法并由 SQL 与 InMemory 两种实现同时提供；SQL 实现过滤、分页、计数全部在数据库内完成，禁止读全量后切片。由 `TaskControlService` 消费；不另建命令表、存储体系或新 ABC。
- 返回字段沿用单条回执白名单：command_id、kind、state、issued_at、issuer、task_ref、run_ref、expires_at、expected_revision；不返回 `payload`、`payload_schema_ref`、`detail_ns`、`extra` 等内部内容；存储失败明确报错，不得伪装成空列表。

## Capabilities

### Modified Capabilities

- `platform-task-control`: 新增"按 Task 分页查询命令回执的只读 API" requirement——查询入口、五状态覆盖、稳定排序与分页、Task 可见性 404 语义、白名单字段、列表只读边界（不推进状态/不做懒过期）与存储失败报错。既有命令下发与单条查询 requirement 不变。
- `durable-execution-storage`: 新增"命令回执按 Task 的存储层列表查询" requirement——`ControlCommandRecorder` 接缝扩展（列表方法进入 ABC）、SQL 在库内完成过滤/分页/计数、完整 Task 引用与 workspace 双重限定、InMemory 语义逐点一致（参数化钉住）、只读无副作用。

### New Capabilities

（无。）

## Impact

- **修改**：`packages/hecate-durable/src/hecate_durable/seams.py`（ABC 增加列表方法）、`packages/hecate-durable/src/hecate_durable/storage/store.py`（SQL 实现）、`packages/hecate-durable/src/hecate_durable/stub.py`（InMemory 实现）；`src/hecate/execution/task_control.py`（`TaskControlService` 新增查询方法）；`src/hecate/channel/api/tasks.py`（薄路由）。
- **新增**：独立查询测试（存储层参数化套件 + HTTP 套件）及本 change 工件。
- **不修改**：Runtime 动作派发与批处理（#280 的 `accepted_action_step.py` 等）、前端、数据库结构（`CommandRow` 既有列即可，无迁移）；既有 `record`/`transition`/`get`/`list_pending_commands` 行为不变。
- **依赖**：既有 `CommandState` 五值闭枚举与 `ControlCommandRecord` 契约（`durable-execution-contract`，语义不变）。无 CI 门禁变化。
