## MODIFIED Requirements — 修改的需求

### Requirement: 通过 checkpoint 实现中断/恢复 — 通过 checkpoint 实现中断/恢复
运行时 SHALL 支持通过将完整状态持久化到 checkpoint 来实现中断/恢复，中断时保存，恢复时还原。此行为 SHALL 在包含 FAN_OUT/MERGE 节点的图中保持一致。`PostgresCheckpointStore` 的具体实现 SHALL 位于 services 层（`services/checkpoint_store.py`），而非 engine 层。

#### Scenario: Worker 触发中断
- **WHEN** 一个 worker 返回 `Command(interrupt=value)`
- **THEN** 运行时 SHALL 保存包含中断元数据的 checkpoint，输出 `{"type": "interrupt", "value": value}`，并停止执行

#### Scenario: 从中断恢复
- **WHEN** 使用 `resume_value` 调用 `execute()`
- **THEN** 运行时 SHALL 从最后一个 checkpoint 恢复，将 `resume_value` 写入 `_resume_value` channel，并从中断点之后的节点继续执行

#### Scenario: Engine 层没有 PostgresCheckpointStore
- **WHEN** 检查 `engine/checkpoint.py`
- **THEN** 它 SHALL 仅包含 `CheckpointStore` ABC 和 `InMemoryCheckpointStore`
- **AND** 它 SHALL 不导入 `models/`、`services/` 或 `sqlalchemy`

#### Scenario: PostgresCheckpointStore 位于 services 层
- **WHEN** 生产代码需要持久化 checkpoint 存储
- **THEN** 它 SHALL 从 `hecate.services.checkpoint_store` 导入 `PostgresCheckpointStore`
- **AND** 构造函数 SHALL 接受 `session_factory: async_sessionmaker[AsyncSession]`
