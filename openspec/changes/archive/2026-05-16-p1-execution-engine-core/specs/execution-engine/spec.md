## ADDED Requirements — 新增需求

### Requirement: Pregel 运行时超步调度 — Pregel Runtime Superstep Scheduling

执行引擎 SHALL 实现 Pregel/BSP 模型的超步循环。每个超步 MUST 依次执行：READ → DISPATCH → AWAIT → WRITE → CHECKPOINT → ROUTE。当没有就绪节点时，执行 MUST 结束。
— Execution engine SHALL implement Pregel/BSP superstep loop. Each superstep MUST execute: READ → DISPATCH → AWAIT → WRITE → CHECKPOINT → ROUTE. Execution MUST end when no ready nodes remain.

#### Scenario: 线性三节点图完整执行 — Linear three-node graph execution
- **WHEN** 执行 `A → B → C` 线性图的 CompiledGraph
- **THEN** 引擎 MUST 依次执行 3 个超步，每个超步执行 1 个节点

#### Scenario: 条件分支节点路由 — Conditional branch routing
- **WHEN** condition 节点返回路由键 `"branch_a"`
- **THEN** 引擎 MUST 只调度 `"branch_a"` 指向的目标节点

### Requirement: Channel 状态管理 — Channel State Management

Channel 系统 SHALL 管理 Graph 执行期间的所有状态。每个 Channel MUST 支持 `read()` 和 `write()`。Channel 写入 MUST 遵循类型语义。支持 `injectable` Channel 用于外部注入值。
— Channel system SHALL manage all state during Graph execution.

#### Scenario: 节点读取只读 Channel 快照 — Node reads read-only Channel snapshot
- **WHEN** Worker 执行节点时调用 `channel_snapshot["messages"]`
- **THEN** MUST 返回该 Channel 在当前超步开始时的值

#### Scenario: injectable Channel 接收外部输入 — Injectable Channel receives external input
- **WHEN** 用户通过 API 恢复中断的执行并传入 `resume_value`
- **THEN** 引擎 MUST 将 `resume_value` 注入到对应的 injectable Channel

### Requirement: Checkpoint 持久化 — Checkpoint Persistence

引擎 MUST 在每个 superstep 完成后将 Checkpoint 写入 PostgreSQL。Checkpoint 一旦写入 MUST 不可修改。引擎 SHALL 维护内存缓存加速恢复。
— Engine MUST persist Checkpoint to PostgreSQL after each superstep. Checkpoints MUST be immutable. Engine SHALL maintain in-memory cache.

#### Scenario: 内存缓存加速恢复读取 — In-memory cache accelerates recovery
- **WHEN** 连续执行两个超步后请求读取最新 Checkpoint
- **THEN** 引擎 SHALL 从内存缓存返回结果

### Requirement: interrupt 与恢复 — Interrupt and Resume

节点 SHALL 能通过 `interrupt(value)` 暂停执行。调用 interrupt 后，引擎 MUST 立即停止 Pregel 循环、保存 Checkpoint、返回 interrupt value。恢复时通过 `Command(resume=value)` 传入恢复值。
— Node SHALL pause execution via `interrupt(value)`. Engine MUST stop Pregel loop, save Checkpoint, return interrupt value.

#### Scenario: 用户审批后恢复执行 — Resume after user approval
- **WHEN** 用户发送 `Command(resume="approved")` 恢复中断的 Session
- **THEN** 引擎 MUST 将 `"approved"` 作为 interrupt 的返回值注入

### Requirement: EnginePort 接口 — EnginePort Interface

引擎 MUST 通过 `EnginePort` 接口调用能力服务层。EnginePort SHALL 定义：`llm_invoke()`, `tool_execute()`, `knowledge_query()`, `checkpoint_save/load()`, `conversation_load/save()`。不得直接依赖具体服务实现。
— Engine MUST call service layer through `EnginePort` interface. No direct dependency on service implementations.

#### Scenario: EnginePort 解耦引擎与服务层 — EnginePort decouples engine from services
- **WHEN** 替换模型路由实现（如从 LiteLLM 切换到自定义路由）
- **THEN** 执行引擎代码 MUST 无需任何修改

### Requirement: 流式输出 — Streaming Output

引擎 SHALL 支持 4 种流式输出模式：`values`、`updates`、`messages`、`debug`。通过 SSE 推送给客户端。
— Engine SHALL support 4 streaming modes via SSE.

#### Scenario: messages 模式实时推送 LLM 输出 — Messages mode pushes LLM output in real-time
- **WHEN** 客户端请求 stream=messages 模式
- **THEN** 引擎 MUST 在每个 Token 生成时推送 SSE 事件

### Requirement: 子图支持 — Subgraph Support (P1 framework)

引擎 SHALL 预留子图执行基础接口。`agent` 类型节点 MUST 支持加载被引用 Agent 的 Graph 作为子图执行。P1 子图 MUST 在同一进程中同步执行。
— Engine SHALL reserve subgraph execution interfaces. Subgraphs MUST execute synchronously in same process in P1.

#### Scenario: agent 节点触发子图执行 — Agent node triggers subgraph execution
- **WHEN** 执行到 `agent` 类型节点，其 `agent_ref` 指向另一个已定义的 Agent
- **THEN** 引擎 MUST 加载该 Agent 的 CompiledGraph 作为子图执行
