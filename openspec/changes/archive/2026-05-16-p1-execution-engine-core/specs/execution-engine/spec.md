## ADDED Requirements

### Requirement: Pregel 运行时超步调度

执行引擎 SHALL 实现 Pregel/BSP 模型的超步循环。每个超步 MUST 依次执行：READ（各节点读取 Channel 当前值）→ DISPATCH（将就绪节点分发到 Worker Pool）→ AWAIT（等待所有 Worker 返回）→ WRITE（将 Worker 输出写入 Channel）→ CHECKPOINT（持久化当前状态）→ ROUTE（根据条件边决定下一步）。当没有就绪节点时，执行 MUST 结束。

#### Scenario: 线性三节点图完整执行
- **WHEN** 执行 `A → B → C` 线性图的 CompiledGraph
- **THEN** 引擎 MUST 依次执行 3 个超步，每个超步执行 1 个节点，最终输出最后一个节点的结果

#### Scenario: 条件分支节点路由
- **WHEN** 执行包含 condition 节点的图，condition 节点返回路由键 `"branch_a"`
- **THEN** 引擎 MUST 在下一个超步只调度 `"branch_a"` 指向的目标节点

### Requirement: Channel 状态管理

Channel 系统 SHALL 管理 Graph 执行期间的所有状态。每个 Channel MUST 支持 `read()` 和 `write()` 操作。Channel 写入 MUST 遵循其类型语义（last_value 覆盖、topic 追加、accumulator 聚合）。节点 MUST 声明其读取的 Channel（readable）和写入的 Channel（writable）。支持 `injectable` Channel 用于外部注入值（如用户输入）。

#### Scenario: 节点读取只读 Channel 快照
- **WHEN** Worker 执行节点时调用 `channel_snapshot["messages"]`
- **THEN** MUST 返回该 Channel 在当前超步开始时的值，不受同超步其他节点写入影响

#### Scenario: injectable Channel 接收外部输入
- **WHEN** 用户通过 API 恢复中断的执行并传入 `resume_value`
- **THEN** 引擎 MUST 将 `resume_value` 注入到对应的 injectable Channel 中

### Requirement: Checkpoint 持久化

执行引擎 MUST 在每个 superstep 完成后将 Checkpoint 写入 PostgreSQL。Checkpoint MUST 包含 `session_id`、`superstep` 编号、`node_id`、`channel_state`（所有 Channel 当前值序列化为 JSONB）、`pending_writes`、`metadata`。Checkpoint 一旦写入 MUST 不可修改。引擎 SHALL 维护内存缓存，缓存最近一次 Checkpoint 以加速恢复读取。

#### Scenario: 超步完成后自动写 Checkpoint
- **WHEN** superstep 3（执行节点 `"plan"`）完成
- **THEN** 引擎 MUST 写入一条 Checkpoint 记录，superstep=3，node_id="plan"，channel_state 包含当前所有 Channel 值

#### Scenario: 从 Checkpoint 恢复执行
- **WHEN** Session 中断后用户请求恢复
- **THEN** 引擎 MUST 从 PostgreSQL 加载最新 Checkpoint，重建 Channel 状态，从断点的下一个超步继续执行

#### Scenario: 内存缓存加速恢复读取
- **WHEN** 连续执行两个超步后请求读取最新 Checkpoint
- **THEN** 引擎 SHALL 从内存缓存返回结果，不访问 PostgreSQL

### Requirement: interrupt 与恢复

节点 SHALL 能通过 `interrupt(value)` 暂停执行。调用 interrupt 后，引擎 MUST 立即停止 Pregel 循环、保存当前 Checkpoint、将 interrupt value 返回给调用方。恢复时，调用方 MUST 通过 `Command(resume=value)` 传入恢复值，引擎从断点继续执行。

#### Scenario: 高风险操作触发 interrupt
- **WHEN** Agent 节点检测到工具调用风险等级为 HIGH，调用 `interrupt({"type": "approval", "operation": "delete_data"})`
- **THEN** 引擎 MUST 暂停执行，Session 状态变为 `interrupted`，API 返回 interrupt 信息

#### Scenario: 用户审批后恢复执行
- **WHEN** 用户通过 API 发送 `Command(resume="approved")` 恢复中断的 Session
- **THEN** 引擎 MUST 将 `"approved"` 作为 interrupt 的返回值注入，从中断节点继续执行后续超步

### Requirement: EnginePort 接口

执行引擎 MUST 通过 `EnginePort` 接口调用能力服务层。EnginePort SHALL 定义以下方法：`llm_invoke(messages, config) → Stream[Token]`、`tool_execute(name, args, context) → Result`、`knowledge_query(query, kb_ids) → List[Chunk]`、`checkpoint_save(state) → CheckpointId`、`checkpoint_load(id) → State`、`conversation_load(session_id) → ConversationHistory`、`conversation_save(session_id, messages) → void`。所有外部能力调用 MUST 通过 EnginePort，引擎内部不得直接依赖具体服务实现。

#### Scenario: 节点通过 EnginePort 调用 LLM
- **WHEN** conversation 节点需要调用 LLM 生成回复
- **THEN** 引擎 MUST 通过 `EnginePort.llm_invoke()` 发起调用，获取 streaming Token 输出

#### Scenario: EnginePort 解耦引擎与服务层
- **WHEN** 替换模型路由实现（如从 LiteLLM 切换到自定义路由）
- **THEN** 执行引擎代码 MUST 无需任何修改，仅替换 EnginePort 的实现类

### Requirement: 流式输出

执行引擎 SHALL 支持 4 种流式输出模式：`values`（每个 superstep 后完整状态）、`updates`（每个 superstep 增量更新）、`messages`（LLM Token 流）、`debug`（内部执行细节）。流式输出 MUST 通过 SSE（Server-Sent Events）推送给客户端。

#### Scenario: messages 模式实时推送 LLM 输出
- **WHEN** 客户端请求 stream=messages 模式执行 Graph
- **THEN** 引擎 MUST 在 LLM 生成每个 Token 时通过 SSE 推送 `data: {"type": "token", "content": "..."}` 事件

#### Scenario: updates 模式推送超步增量
- **WHEN** 客户端请求 stream=updates 模式
- **THEN** 每个超步完成后引擎 MUST 推送 `data: {"type": "update", "node": "plan", "output": {...}}` 事件

### Requirement: 子图支持（P1 基础框架）

执行引擎 SHALL 预留子图执行的基础接口。`agent` 类型节点在执行时 MUST 支持加载被引用 Agent 的 Graph 并作为子图执行。子图执行 SHALL 支持状态映射（parent Channel → child Channel 输入，child Channel → parent Channel 输出）。P1 阶段子图 MUST 在同一进程中同步执行。

#### Scenario: agent 节点触发子图执行
- **WHEN** 执行到 `agent` 类型节点，其 `agent_ref` 指向另一个已定义的 Agent
- **THEN** 引擎 MUST 加载该 Agent 的 CompiledGraph，将 parent 的 messages Channel 映射为子图输入，执行子图，将子图输出映射回 parent Channel
