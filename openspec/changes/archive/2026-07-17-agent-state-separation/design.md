## Context — 背景

Hecate 当前每会话状态存在于临时 `execution_context` 字典中，每次 `WorkflowExecutionService.execute()` 调用时都会新创建。当进程退出时，所有工作状态都会丢失 — 对话缓冲区、压缩摘要、权限缓存以及工具/任务的子上下文。只有持久存储（DB 消息、用于图执行的 CheckpointStore、AgentEnvironment 文件系统）得以保留。

这与竞争平台相比存在差距：
- **AgentScope 2.0**：`AgentState`（Pydantic）带 `AgentStateStore`（InMemory/File/Redis/MySQL/OSS）
- **Claude Code**：`SessionStore` 适配器（S3/Redis/Postgres）带双写架构
- **Bedrock AgentCore**：托管会话存储（每会话文件系统，14 天保留期）
- **Codex**：Rollout 系统（JSONL + SQLite 索引，仅追加）

`execution_context` 已经包含了 AgentState 的雏形：`session_id`、`context_engine`、`environment_root`。差距在于结构、持久化和生命周期管理。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 引入 `AgentState` 作为结构化 Pydantic 模型，表示每会话工作状态
- 定义 `AgentStateStore` ABC 用于可插拔的状态持久化
- 实现 `InMemoryStateStore` 用于单机使用和测试
- 将状态加载/保存生命周期集成到 `WorkflowExecutionService`
- 自动将 `EnvironmentManager` 的 `environment_root` 填充到 AgentState

**非目标（推迟到后续特性）：**
- Redis/Postgres 状态存储后端（→ 13.4a 分布式会话状态存储）
- 压缩摘要实现（→ ContextEngine 增强）
- 状态检查 REST API（→ 仅内部服务层）
- 多租户状态隔离（→ 10.5 租户隔离）
- 状态序列化格式优化（MVP 中使用 JSON 通过 Pydantic 已足够）

## Decisions — 决策

### D1：AgentStateStore 在 services/（而非 engine/）

**决策**：将 `AgentStateStore` ABC 和实现放在 `src/hecate/services/state/`。

**理由**：引擎层不能从 services/ 导入（分层规则）。AgentState 需要引用 `AgentEnvironment`（在 services/ 中），并会被 `WorkflowExecutionService`（在 services/ 中）消费。将其放在 services/ 可避免分层违规。引擎的 `CheckpointStore`（图级别）是正交的，保持在 engine/ 中。

**考虑的替代方案**：将 ABC 放在 `engine/ports.py`。拒绝，因为这将要求引擎了解服务层概念（EnvironmentManager）。

### D2：AgentState 作为 execution_context 字段（而非替换）

**决策**：AgentState 注入到 `execution_context["_agent_state"]`。Worker 通过 `execution_context` 间接访问它。

**理由**：对现有 Worker 接口的最小更改。Worker 已接收 `execution_context` 作为字典 — 添加键不会破坏兼容性。完全替换 `execution_context` 将需要更改每个 Worker 的 `execute()` 签名。

**考虑的替代方案**：使 AgentState 成为新的 `execution_context` 类型。拒绝，因为这改变了 Worker 契约，对于 MVP 来说改动过大。

### D3：摘要字段预留，不实现

**决策**：AgentState 包含一个 `summary: str` 字段，在 MVP 中始终为空。ContextEngine 增强将在后续填充它。

**理由**：该字段存在于数据模型中（以便消费者可以开始对其编写代码），但尚未实现压缩逻辑。这避免了过早将 AgentState 与 ContextEngine 内部耦合。

### D4：每次调用写入策略

**决策**：AgentState 在 `execute()` 入口处加载，在 `execute()` 退出时保存（每次调用一次）。不在每条消息或通道写入时保存。

**理由**：与 AgentScope 的模式一致。减少存储压力。调用是自然的原子单元 — 如果进程在调用中崩溃，前一个状态仍然有效（调用将被重试）。

**考虑的替代方案**：在每个超步骤保存（如 LangGraph）。拒绝，因为它为 MVP 增加了延迟和复杂度。

### D5：Pydantic BaseModel 用于序列化

**决策**：AgentState 继承 Pydantic `BaseModel`，使用 `model_dump()` / `model_validate()` 进行序列化。

**理由**：与 AgentScope 的方法一致。Pydantic 处理类型验证、JSON 序列化和模式演化。已作为项目依赖可用。

### D6：asyncio.Lock 用于并发访问安全

**决策**：InMemoryStateStore 使用每个 session_id 的 `asyncio.Lock` 以防止并发写入。

**理由**：多个协程可能访问同一会话（例如，流式处理 + 后台保存）。锁是每个键的，而非全局的，因此不同会话可以并发保存。

## Risks / Trade-offs — 风险 / 权衡

| 风险 | 缓解措施 |
|------|-----------|
| InMemoryStateStore 在进程重启后丢失状态 | MVP 预期行为。Redis/Postgres 后端推迟到 13.4a。记录限制。 |
| AgentState 大小增长（大型上下文列表） | 由未来的 ContextEngine 压缩缓解。MVP 没有大小限制 — 需要时添加。 |
| 并发保存冲突 | asyncio.Lock 防止损坏。不适用于分布式 — 单进程 MVP 可接受。 |
| execution_context 字典突变泄漏状态 | AgentState 是 Pydantic 模型（复制语义）。Worker 中的突变不会影响存储的快照，直到显式保存。 |

## Migration Plan — 迁移计划

无需迁移 — 这是纯新增功能。现有的 `execution_context` 行为不变。AgentState 是可选的：如果未提供 `AgentStateStore`，`WorkflowExecutionService` 的行为与之前完全相同。

## Open Questions — 待定问题

- **状态大小监控**：当 AgentState 超过阈值时是否应添加警告？（推迟到可观测性阶段）
- **旧会话的垃圾回收**：InMemoryStateStore 会无限制增长。是否添加 TTL 驱逐？（推迟到 EnvironmentManager 的 TTL 模式）
