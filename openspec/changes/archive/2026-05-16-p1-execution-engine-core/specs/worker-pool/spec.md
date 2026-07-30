## ADDED Requirements — 新增需求

### Requirement: Worker 接口定义 — Worker Interface Definition

Worker 接收 `WorkerTask`（task_id, session_id, node_id, node_type, node_config, channel_snapshot, deadline），返回 `WorkerResult`（task_id, status: success/error/timeout/interrupted, output, metadata, error）。
— Worker receives `WorkerTask` and returns `WorkerResult`.

#### Scenario: Worker 成功执行并返回结果 — Worker executes successfully
- **WHEN** Worker 执行 conversation 节点，LLM 成功返回回复
- **THEN** Worker 返回 `WorkerResult(status="success", output=..., metadata={"tokens": 150, "latency_ms": 1200})`

### Requirement: P1 线程池实现 — P1 Thread Pool Implementation

P1 MUST 使用 `concurrent.futures.ThreadPoolExecutor`。默认线程数 `min(32, os.cpu_count() + 4)`。支持配置最大线程数。
— P1 MUST use ThreadPoolExecutor with configurable max workers.

#### Scenario: 多就绪节点并行执行 — Parallel execution of multiple ready nodes
- **WHEN** superstep 中有 3 个无依赖的就绪节点
- **THEN** 调度器 MUST 并行执行 3 个任务，等待全部完成后写入 Channel

### Requirement: Worker 接收只读 Channel 快照 — Worker Receives Read-Only Channel Snapshot

Worker 只接收只读快照（深拷贝），不直接修改 Channel。输出通过 `WorkerResult.output` 由 Scheduler 统一写入。
— Worker receives read-only snapshot. Output written to Channel by Scheduler.

### Requirement: interrupt 信号传递 — Interrupt Signal Delivery

Worker 通过 `WorkerResult(status="interrupted")` 通知 Scheduler。interrupt value 封装在 `output["__interrupt__"]` 中。Scheduler 收到后停止循环并保存 Checkpoint。
— Worker signals interrupt via WorkerResult. Scheduler stops loop and saves Checkpoint.

#### Scenario: Scheduler 响应 interrupt 停止循环 — Scheduler responds to interrupt
- **WHEN** Scheduler 检测到任一个 WorkerResult 的 status="interrupted"
- **THEN** Scheduler MUST 取消其他任务，保存 Checkpoint，停止循环

### Requirement: Worker 无状态可重调度 — Worker Stateless and Reschedulable

Worker MUST 不保存任何执行状态。所有输入通过 `WorkerTask` 传入，输出通过 `WorkerResult` 返回。失败时可基于 Checkpoint 重新构造 WorkerTask 重试。
— Worker MUST be stateless. All input via WorkerTask, output via WorkerResult. Reschedulable from Checkpoint.

#### Scenario: Worker 失败后重新调度 — Reschedule on Worker failure
- **WHEN** Worker 抛出未捕获异常，返回 `WorkerResult(status="error")`
- **THEN** Scheduler MUST 根据重试策略决定是否重试
