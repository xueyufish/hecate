## ADDED Requirements

### Requirement: Worker 接口定义

Worker Pool SHALL 定义统一的 Worker 接口。Worker 接收 `WorkerTask` 作为输入，返回 `WorkerResult`。`WorkerTask` MUST 包含 `task_id`（UUID）、`session_id`（UUID）、`node_id`（NodeId）、`node_type`（NodeType）、`node_config`（dict）、`channel_snapshot`（dict，只读 Channel 快照）、`deadline`（可选超时时间戳）。`WorkerResult` MUST 包含 `task_id`、`status`（`success` | `error` | `timeout` | `interrupted`）、`output`（dict，Channel 写入内容）、`metadata`（dict，Token 用量/耗时等）、`error`（可选 ErrorInfo）。

#### Scenario: Worker 成功执行并返回结果
- **WHEN** Worker 执行 conversation 节点，LLM 成功返回回复
- **THEN** Worker MUST 返回 `WorkerResult(status="success", output={"messages": [response]}, metadata={"tokens": 150, "latency_ms": 1200})`

#### Scenario: Worker 执行超时
- **WHEN** Worker 收到的 task 设置 deadline 为当前时间 +30 秒，且 LLM 调用在 30 秒内未返回
- **THEN** Worker MUST 返回 `WorkerResult(status="timeout", error={"message": "execution exceeded deadline"})`

### Requirement: P1 线程池实现

P1 阶段 Worker Pool MUST 使用进程内线程池实现。线程池 SHALL 基于 `concurrent.futures.ThreadPoolExecutor`。默认线程数 MUST 为 `min(32, os.cpu_count() + 4)`。线程池 MUST 支持通过配置调整最大线程数。

#### Scenario: 多就绪节点并行执行
- **WHEN** 一个 superstep 中有 3 个无依赖的就绪节点
- **THEN** 调度器 MUST 将 3 个 WorkerTask 提交到线程池并行执行，等待全部完成后统一写入 Channel

#### Scenario: 线程池满时排队等待
- **WHEN** 线程池最大线程数配置为 4，同时有 6 个就绪节点
- **THEN** 前 4 个任务 MUST 立即执行，后 2 个任务 MUST 在队列中等待，直到有线程空闲

### Requirement: Worker 接收只读 Channel 快照

Worker MUST 只接收 Channel 的只读快照，不直接修改 Channel。`WorkerTask.channel_snapshot` SHALL 是当前超步开始时 Channel 状态的深拷贝。Worker 的所有输出 MUST 通过 `WorkerResult.output` 返回，由 Scheduler 统一写入 Channel。

#### Scenario: Worker 写入不影响其他并发 Worker
- **WHEN** Worker A 和 Worker B 并行执行，Worker A 向 `WorkerResult.output` 写入 `{"plan": "step1"}`
- **THEN** Worker B 的 `channel_snapshot` MUST NOT 受 Worker A 写入影响

#### Scenario: Scheduler 统一收集 Worker 输出写入 Channel
- **WHEN** 所有并行 Worker 返回结果
- **THEN** Scheduler MUST 依次将每个 WorkerResult.output 中的值写入对应 Channel，遵循 Channel 类型语义

### Requirement: interrupt 信号传递

Worker MUST 通过 `WorkerResult.status="interrupted"` 通知 Scheduler 暂停执行。当 Worker 内部调用 `interrupt(value)` 时，Worker SHALL 将 interrupt value 封装到 `WorkerResult.output` 中（key 为 `"__interrupt__"`），status 设为 `"interrupted"`。Scheduler 收到后 MUST 立即停止当前 Pregel 循环并保存 Checkpoint。

#### Scenario: Worker 触发 interrupt 信号
- **WHEN** Worker 内部检测到高风险操作，调用 `interrupt({"type": "approval"})`
- **THEN** Worker MUST 返回 `WorkerResult(status="interrupted", output={"__interrupt__": {"type": "approval"}})`

#### Scenario: Scheduler 响应 interrupt 停止循环
- **WHEN** Scheduler 从任一 WorkerResult 中检测到 `status="interrupted"`
- **THEN** Scheduler MUST 取消当前超步的其他进行中任务，保存 Checkpoint，停止 Pregel 循环

### Requirement: Worker 无状态可重调度

Worker MUST 不保存任何执行状态。Worker 的所有输入通过 `WorkerTask` 传入，所有输出通过 `WorkerResult` 返回。当 Worker 因故障失败时，Scheduler MUST 能基于 Checkpoint 重新构造 `WorkerTask` 并提交到线程池重新执行。

#### Scenario: Worker 失败后重新调度
- **WHEN** Worker 执行过程中抛出未捕获异常，返回 `WorkerResult(status="error")`
- **THEN** Scheduler MUST 根据重试策略决定是否重新构造相同的 WorkerTask 提交执行
