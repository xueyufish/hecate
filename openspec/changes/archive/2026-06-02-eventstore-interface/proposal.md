## Why — 动机

引擎当前使用基于**快照**的 checkpoint 模型（`CheckpointStore.save/load`），在离散的超级步边界捕获完整的 channel 状态。这对于暂停/恢复已经足够，但排除了多个 P2+ 能力：细粒度审计追踪、增量状态重建、事件驱动调试和基于重放的测试。仅追加的 EventStore 接口以最小的实现成本（P2 中仅为 ABC）为这些能力提供了基础。

## What Changes — 变更内容

- 在 `engine/eventstore.py` 中添加新的 `EventStore` ABC，包含追加、查询和重放事件的方法
- 添加用于测试的 `InMemoryEventStore` 实现
- 将 EventStore 注册为与 CheckpointStore 并列的可选引擎依赖
- 不修改现有的 CheckpointStore 或 PregelRuntime——EventStore 是附加性的，可独立使用

## Capabilities — 能力变更

### 新增能力
- `eventstore`: 仅追加的事件持久化接口，用于细粒度执行状态跟踪

### 修改的能力
- `engine-ports`: 为 EnginePort 添加可选的 `event_store` 属性，用于 P2 接口预留

## Impact — 影响范围

- **新文件**: `src/hecate/engine/eventstore.py`（ABC + InMemoryEventStore）
- **修改的文件**: `src/hecate/engine/ports.py`（添加可选的 `event_store` 属性）
- **新测试**: `tests/test_engine/test_eventstore.py`
- **无破坏性变更**: EventStore 完全是附加性的；现有代码无需修改
- **无新依赖**: 仅使用 stdlib（`abc`、`uuid`、`dataclasses`）