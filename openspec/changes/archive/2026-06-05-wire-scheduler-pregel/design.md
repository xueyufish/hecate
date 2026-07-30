## Context — 上下文

`SchedulerStrategy` ABC 和 `FIFOScheduler` 存在于 `engine/scheduler.py` 中（在变更 `2026-06-02-scheduler-strategy-interface` 中实现），但尚未接入 `PregelRuntime`。运行时的 `execute()` 方法在第 144 行直接迭代 `current_nodes`：

```python
for node_id in current_nodes:
    # 调度每个节点...
```

已发布的规范（`openspec/specs/scheduler-strategy/spec.md`）已经声明了 "PregelRuntime 应接受可选的 `scheduler` 参数"，但代码并未实现这一点。本变更填补了这一差距。

两个服务实例化 `PregelRuntime`：
- `services/workflow/execution_service.py` 第 216 行
- `services/workflow/test_runner.py` 第 199 行

两者必须通过默认参数保持兼容。

## Goals / Non-Goals — 目标/非目标

**目标：**
- 将 `SchedulerStrategy` 接入 `PregelRuntime` 构造函数和超步循环
- 将执行上下文（超步编号、通道快照）传递给 `select_next`
- 通过集成测试验证调度器在执行期间被调用
- 保持现有行为不变（FIFOScheduler 是恒等函数）

**非目标：**
- 节点并行执行（WorkerPool 的职责）
- 异步 `select_next`（根据引擎零依赖原则保持同步）
- 类型化上下文对象（根据 YAGNI 原则保持 `dict`——尚无真实调度器实现来驱动字段发现）
- 调度器感知 FAN_OUT/MERGE 节点类型（不需要——`_resolve_next_nodes()` 保证同超步节点语义独立）
- 执行期间的动态权重变更（P3+）

## Decisions — 决策

### D1：调度器是可选的构造函数参数，默认使用 FIFOScheduler

**选择**：`scheduler: SchedulerStrategy | None = None` → 存储为 `self._scheduler = scheduler or FIFOScheduler()`

**考虑的替代方案**：
- 必需参数 → 被拒绝：破坏现有的实例化点
- 无参数，始终使用 FIFOScheduler → 被拒绝：违背了可插拔的目的

**理由**：可选带默认值保持了向后兼容性。`None` 哨兵避免了可变默认参数问题，并使调用点的意图清晰明确。

### D2：每个超步调用一次 `select_next`，在 `for` 循环之前

**选择**：在第 144 行用 `scheduled_nodes = self._scheduler.select_next(current_nodes, context)` 替换 `current_nodes`，然后迭代 `scheduled_nodes`。

**考虑的替代方案**：
- 在循环内每个节点调用 → 被拒绝：N 个节点 N 次调用，有开销无收益
- 将整个超步块包装在调度器方法中 → 被拒绝：过度设计，调度器只需要排序，不需要编排

**理由**：单次调用简洁且符合 `select_next` 的契约（接受列表，返回有序列表）。调度器重新排序但不过滤——它总是返回相同的节点 ID。

### D3：上下文字典包含超步编号和通道快照

**选择**：`context = {"superstep": self._superstep, "channel_snapshot": snapshot}`

**考虑的替代方案**：
- 类型化数据类 → 被拒绝：YAGNI——尚无调度器实现来驱动字段发现
- 空字典 → 被拒绝：对任何非平凡调度器都无用
- 包含图元数据（节点配置、边列表）→ 被拒绝：过度暴露引擎内部

**理由**：超步编号支持优先级衰减策略。通道快照支持内容感知调度。两者提供成本都很低。未来的调度器可以忽略它们不需要的键。

### D4：调度器不过滤或拒绝节点

**选择**：`select_next` 必须返回相同的节点 ID 集合（可能重新排序）。运行时不验证这一点，但丢弃节点的调度器会导致图执行停滞。

**理由**：过滤是调度关注点，但我们没有用例。如果在 P3 中需要，添加 `filter_next` 方法而不是重载 `select_next`。

### D5：FAN_OUT/MERGE 节点透明地通过调度器传递

**选择**：调度器接收所有 `current_nodes`，包括任何 FAN_OUT/MERGE 节点。循环内的特殊处理（第 151-159 行）在调度之后运行。

**理由**：`_resolve_next_nodes()` 保证同一 `current_nodes` 列表中的节点在语义上是独立的。FAN_OUT 分支由 `_dispatch_fan_out()` 内部调度——它们不会出现在 `current_nodes` 中。在同一超步内重新排序 FAN_OUT/MERGE 与普通节点之间的顺序是安全的。

## Risks / Trade-offs — 风险/权衡

| 风险 | 缓解措施 |
|------|---------|
| 调度器丢弃节点，导致执行停滞 | 明确记录契约；FIFOScheduler 保证透传；自定义调度器采用信任模型 |
| 上下文字典键将来可能变更 | 记录当前键；视为建议性——调度器应优雅处理未知键 |
| 单节点超步上调度器调用的开销 | FIFOScheduler 是 O(1) 透传；与 LLM 调用相比可忽略 |
