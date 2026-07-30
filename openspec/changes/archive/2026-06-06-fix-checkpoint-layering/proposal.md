## Why — 动机

`engine/checkpoint.py` 包含一个 `PostgresCheckpointStore`，它在模块级别导入了 `sqlalchemy` 和 `hecate.models.checkpoint.CheckpointModel`，违反了 engine 层的"零外部依赖（不从 services/、api/、models/ 导入）"规则。这个违规是在 P2 中引入的（提交 `df40810`），当时在 `engine/` 内部创建了第二个 `PostgresCheckpointStore`——忽略了已经存在于正确位置 `services/checkpoint_store.py` 的 P1 实现。

## What Changes — 变更内容

- **从 `engine/checkpoint.py` 移除 `PostgresCheckpointStore`** — 将文件恢复到 P1 状态（仅包含 ABC + InMemoryCheckpointStore，零外部依赖）
- **升级 `services/checkpoint_store.py`** — 将 engine 版本的改进（基于 `OrderedDict` 的可配置大小 LRU 缓存、`load()` 的缓存未命中 DB 回退、`_checkpoint_to_dict` 辅助方法）合并到 services 版本中
- **移动测试** — 从 `tests/test_engine/test_postgres_checkpoint.py` 移至 `tests/test_services/test_checkpoint_store.py`，并更新导入以引用 `services.checkpoint_store`
- **适配 `session_factory` 构造函数** — services 版本使用 `async_sessionmaker`（自管理会话 + 提交）而不是原始的 `AsyncSession`

## Capabilities — 能力变更

### 新增能力

（无）

### 修改的能力

- `pregel-runtime`: CheckpointStore 实现位置已明确 — PregelRuntime 从 engine 层获取 `CheckpointStore` ABC；具体的 `PostgresCheckpointStore` 由 services 层提供
- `engine-ports`: 没有规范变更，但与 ABC 错误放置在同一位置的 `PostgresCheckpointStore` 已从 engine 层移除

## Impact — 影响范围

- **移除的代码**: 从 `engine/checkpoint.py` 移除 `PostgresCheckpointStore` 类（~170 行）
- **修改的代码**: `services/checkpoint_store.py` 获得 LRU 缓存和缓存未命中 DB 回退（净增约 40 行）
- **移动的代码**: 测试文件从 `tests/test_engine/` 移至 `tests/test_services/`，更新了导入并适配了构造函数
- **零破坏性变更**: `CheckpointStore` ABC 和 `InMemoryCheckpointStore` 保持不变；`PregelRuntime` 仅依赖 ABC
- **无依赖变更**: `sqlalchemy` 和 `hecate.models.checkpoint` 都已经对 services 层可用
