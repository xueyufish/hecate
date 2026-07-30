## Context — 背景

Hecate 的引擎层当前通过 `CheckpointStore` 持久化执行状态——这是一种基于快照的模型，在离散的时间点捕获完整的 channel 状态、超级步计数器和待处理写入。这种设计适用于暂停/恢复（中断），但无法支持：

- **细粒度审计追踪**: Checkpoint 是不透明的 blob；你无法看到*哪个*工具调用产生了*哪个*中间结果。
- **增量状态重建**: 加载 checkpoint 需要完整的快照；无法从已知点进行部分重放。
- **事件驱动调试**: 无法订阅状态变更的发生。
- **基于重放的测试**: 无法从事件 N 重放执行以验证行为。

引擎的架构是端口和适配器模式：`EnginePort`（ABC）将引擎与服务解耦。`CheckpointStore`（ABC）将持久化解耦。添加 `EventStore` 遵循完全相同的模式。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 定义包含追加、查询和重放方法的 `EventStore` ABC
- 定义捕获细粒度执行状态的 `Event` 数据类（节点开始/结束、工具调用/结果、channel 写入、LLM 请求/响应）
- 提供用于测试的 `InMemoryEventStore`
- 在 `EnginePort` 上预留接口作为可选属性
- 保持引擎零依赖（无外部库）

**非目标：**
- Postgres 后端的 EventStore 实现（P3）
- 与 PregelRuntime 集成以自动发出事件（P3，当 GuardrailHook 实现时）
- 事件 schema 版本控制或迁移
- 事件压缩或保留策略
- 分布式事件流（Kafka/NATS）

## Decisions — 设计决策

### D1：EventStore 是独立的 ABC，不是 CheckpointStore 的扩展

**选择**：创建 `engine/eventstore.py`，与 `engine/checkpoint.py` 并列。

**理由**：CheckpointStore（快照）和 EventStore（流）有本质上不同的 API 和存储需求。将它们分开可保持各自的接口精炼且无耦合。

### D2：Event 数据类包含元数据，而非泛型 blob

**选择**：冻结的 Event 数据类，包含具体字段（`session_id`、`superstep`、`event_type`、`node_id`、`timestamp`、`payload`、`version`），加上用于扩展的通用 `payload: dict`。

**理由**：类型安全字段支持查询和过滤而不解析。`payload` 字典允许 EventType 特定的数据而无需每类型子类化。

### D3：版本号用于增量查询和重放

**选择**：每个事件在每个会话中获得一个单调递增的 `version`（从 1 开始）。`get_events(session_id, from_version=0)` 和 `replay(session_id, from_version=0)` 使用此版本号进行增量查询。

**理由**：版本号允许调用者只请求它们尚未看到的事件（即仅增量）。这对于长期运行的会话和重放场景至关重要。

### D4：`replay` 是异步生成器

**选择**：`replay(session_id, from_version=0) -> AsyncGenerator[Event, None]`

**理由**：匹配引擎的异步特性。生成器避免将整个事件流加载到内存中——对于具有数千个事件的长时间会话很重要。

### D5：EnginePort 上的可选属性（非方法）

**选择**：`event_store: EventStore | None = None` 作为属性，默认为 None。

**理由**：与预期的 P3 集成一致：PregelRuntime 将检查 `if port.event_store` 来决定是否发出事件。属性模式比方法更简单（无参数，预期为 None）。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| 事件数据类字段集可能在 P3 中被证明不完整 | payload dict 提供扩展；添加新字段不是破坏性变更 |
| InMemoryEventStore 未针对生产性能调优 | 设计用于测试；P3 PostgresEventStore 将添加索引和批量写入 |
| AsyncGenerator 在非异步上下文中难以测试 | InMemoryEventStore 的 get_events（列表）提供同步替代方案 |