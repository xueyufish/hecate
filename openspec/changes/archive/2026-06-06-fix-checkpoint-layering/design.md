## Context — 背景

`engine/checkpoint.py` 当前包含三个类：
1. `CheckpointStore` ABC — 正确，属于 engine/
2. `InMemoryCheckpointStore` — 正确，测试辅助工具，零外部依赖
3. `PostgresCheckpointStore` — **违规**：导入了 `sqlalchemy` 和 `hecate.models.checkpoint.CheckpointModel`

engine 层规则（来自 AGENTS.md）："零外部依赖（不从 services/、api/、models/ 导入）；jsonschema 是唯一例外。"

同时，`services/checkpoint_store.py` 从 P1 开始就已经有了一个 `PostgresCheckpointStore`，但功能较少（无 LRU 驱逐、无缓存未命中 DB 回退）。它使用 `session_factory: async_sessionmaker` 而不是原始 `AsyncSession`，这是正确的生产模式（自管理事务）。

没有人导入 services 版本。engine 版本仅被 `tests/test_engine/test_postgres_checkpoint.py` 使用。

## Goals / Non-Goals — 目标 / 非目标

**目标：**

1. 消除 engine → models 的层次违规
2. 在 services/ 中生成一个单一的、规范的 `PostgresCheckpointStore`，融合两个版本的最佳特性
3. 确保迁移后所有测试通过

**非目标：**

1. 不修改 `CheckpointStore` ABC 或 `InMemoryCheckpointStore`
2. 不修改 `PregelRuntime`（它依赖的是 ABC，而不是具体类）
3. 不修改 `EnginePort.checkpoint_save/checkpoint_load` 桩代码（属于不同关注点）
4. 不修改 `engine/temporal/run_worker.py`（属于不同的层次违规，不同范围）

## Decisions — 设计决策

### D1: 保留 `session_factory` 构造函数（services 模式）

**选择**：`__init__(self, session_factory: async_sessionmaker[AsyncSession], cache_size: int = 128)`

**理由**：services 版本的 `session_factory` 模式在生产环境中是正确的——它创建并提交自己的会话（自包含事务）。engine 版本的 `db_session: AsyncSession` 模式要求调用者管理事务，这耦合了生命周期管理。

**考虑的替代方案**：保留 `db_session` 以便于测试——被否决，因为这会创建两种构造函数模式，而测试可以轻松适配 `session_factory`。

### D2: 移植 engine 版本的 LRU 缓存

**选择**：使用基于 `OrderedDict` 的 LRU 缓存，可配置 `cache_size`（默认 128）。

**理由**：engine 版本的 LRU 驱逐可防止长时间运行的生产进程中的内存泄漏。services 版本的普通字典没有驱逐——曾访问过的会话会永远留在缓存中。

### D3: 移植 engine 版本的缓存未命中 DB 回退

**选择**：当调用 `load(session_id)` 而没有提供 `checkpoint_id`，且会话不在缓存中时，查询数据库以获取最新的 checkpoint（然后缓存结果）。

**理由**：P1 services 版本仅检查 `_cache.get()`，并在未命中时返回 `None`。P2 engine 版本添加了 DB 回退，这是正确的——缓存未命中不应意味着"不存在 checkpoint"。

### D4: 将测试移至 `test_services/`

**选择**：将 `tests/test_engine/test_postgres_checkpoint.py` 重命名为 `tests/test_services/test_checkpoint_store.py`。

**理由**：`PostgresCheckpointStore` 是一个 services 层的类。其测试属于 `test_services/`。测试文件需要适配构造函数（从 conftest 的 `db_session` fixture 创建 `session_factory`）。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 测试 fixture 适配 — conftest 提供 `AsyncSession`，而不是 `session_factory` | 创建一个辅助 fixture，将 `db_session` 包装到 `async_sessionmaker` 中；测试也可以使用 engine 的 `InMemoryCheckpointStore` 进行纯 engine 测试 |
| Services 版本使用惰性导入 — 首次调用有轻微开销 | 可忽略；惰性导入是 services/ 中用于避免循环依赖的既定模式 |
| `session_factory` 创建独立的会话 — 与调用者没有共享事务 | 这对于 services 层是正确的；checkpoint 持久化应该在事务上独立 |
