## Why

P2 的 PostgreSQL CheckpointStore 只解决了跨节点持久化，但不解决千级并发和长时程推理（数小时/天级任务）。Temporal 集成是 P3 的核心基础设施，解决分布式调度、长时程持久化、冲突调解。

## What Changes

- **新增 `TemporalWorkerPool`**：实现 WorkerPool 接口，将节点执行分发为 Temporal Activities
- **新增 `DistributedPregelWorkflow`**：Temporal Workflow 封装 Pregel 执行，支持 Continue-As-New
- **新增冲突解决策略**：乐观锁、合并、审批、锁机制
- **新增 Temporal 集成配置**：Task Queue、Worker 配置、超时策略

## Capabilities

### New Capabilities

- `temporal-worker-pool`: Temporal Activity 分布式 Worker
- `distributed-pregel`: Temporal Workflow 封装 Pregel 执行
- `conflict-resolution`: 多 Agent 资源冲突解决策略

### Modified Capabilities

（无）

## Impact

- **新增代码**：`engine/temporal/` 目录，约 800-1200 行
- **新增依赖**：`temporalio`（Temporal Python SDK）
- **修改代码**：无（纯新增，Pregel 保留作为单机模式）
- **架构影响**：Pregel 变为可选后端，Temporal 为分布式后端
