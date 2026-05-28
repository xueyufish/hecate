## Context

P2 的 PostgreSQL CheckpointStore 解决了跨节点持久化，但不解决千级并发和长时程推理。Temporal 集成是 P3 的核心基础设施，参考调研报告 01-execution-engine.md 中的 Temporal vs LangGraph 对比。

## Goals / Non-Goals

**Goals:**

1. 实现 TemporalWorkerPool — 将节点执行分发为 Temporal Activities
2. 实现 DistributedPregelWorkflow — Temporal Workflow 封装 Pregel 执行
3. 实现冲突解决策略 — 乐观锁、合并、审批
4. 支持 Continue-As-New 处理长时间运行的工作流

**Non-Goals:**

1. 不删除 PregelRuntime（保留作为单机模式）
2. 不实现 NATS 集成（可选替代方案）
3. 不实现强化学习优化（属于 P4）

## Decisions

### D1: Temporal 作为分布式后端，Pregel 作为单机后端

**选择**：双后端架构，通过 WorkerPool 接口解耦

**理由**：
- 单机场景不需要 Temporal 的复杂性
- Temporal 提供持久化、重试、超时、Continue-As-New
- 接口一致，切换透明

### D2: Pregel 执行封装为 Temporal Activity

**选择**：每个 superstep 封装为一个 Activity

**理由**：
- Activity 有自动重试和超时
- Activity 失败可恢复（从 checkpoint）
- 支持 heartbeat 监控长时间执行

### D3: 冲突解决使用乐观锁 + 合并策略

**选择**：Channel 更新使用乐观锁，可合并类型使用合并策略

**理由**：
- 乐观锁简单高效
- 合并策略适合 topic/accumulator 类型
- 冲突时可请求人工审批

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| Temporal 学习曲线 | 提供详细文档和示例 |
| 活动执行延迟 | 设置合理超时 |
| Continue-As-New 状态丢失 | 使用 PostgreSQL 持久化关键状态 |
