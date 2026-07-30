## Context — 背景

Hecate 的 LLMWorker（引擎层）在每次 LLM 调用之前应用一个 4 步上下文管道：
1. 工具结果截断（将每个工具结果限制在约 2000 tokens）
2. 按预算估算 tokens
3. 消息选择（`ContextEngine.select_messages`）— 在预算内保留最新的消息
4. 压缩（`ContextEngine.compress`）— 最后手段，丢弃最旧的消息

当第 4 步触发时，消息会从 LLM 的视图中永久丢弃。通道保留原始消息（非破坏性管道），但 LLM 没有机制来检索它们 — 没有工具、没有记忆钩子、没有文件指针。这与以下平台形成对比：

- **AgentScope** — `Offloader` 协议将过大的上下文写入文件并返回引用
- **Claude Code** — 基于文件的压缩级联，通过 `read_file` 恢复
- **Amazon Bedrock AgentCore** — 会话状态持久化到 `/mnt/workspace`，可在恢复时检索
- **Letta/MemGPT** — Agent 通过 `memory_store`/`memory_search` 函数工具自行管理记忆

Hecate 现在拥有 `AgentEnvironment`（1.3.15），提供 `write_file`/`read_file` 和每个 Agent 在 `memory/` 下的持久存储。这使得基于文件的卸载成为可能，无需新的基础设施。

**管道的当前状态**（`llm_worker.py` L146-182）：
```
messages → _truncate_tool_results → estimate_tokens → select_messages → compress → LLM
```

`compress` 调用 `InMemoryContextEngine.compress()`，它执行 `messages[-max_messages:]` — 纯粹的截断。

**约束条件：**
- 不得破坏现有的 ContextEngine ABC（被其他管道使用）
- 不得破坏 ConversationService 路径（使用 CompressionPipeline，而非 LLMWorker）
- 必须同时适用于 LocalEnvironment 和 DockerEnvironment
- 引擎层零外部依赖 — 卸载器必须位于 services/ 中，并通过 execution_context 传入

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将溢出的上下文保存在持久存储中，而不是丢弃它
- 允许 Agent 通过现有的 `read_file` 工具按需检索卸载的内容
- 与现有的 LLMWorker 管道干净集成，作为压缩之前的新步骤
- 保持完全向后兼容 — 无环境就不卸载，回退到压缩
- 将卸载决策限定在 LLMWorker 内部（PregelRuntime 无需变更，除上下文注入外）
- 可配置阈值和全局启用/禁用

**非目标：**
- 对卸载的消息进行语义搜索（未来：与 L4 知识记忆集成）
- 自动重新注入已卸载的内容（Agent 必须显式调用 `read_file`）
- 修改 ConversationService 路径（独立的代码路径，已有 CompressionPipeline）
- 更改 ContextEngine ABC（不添加新的抽象方法）
- 通过 LLM 总结已卸载的内容（保持无损；摘要是未来的增强功能）
- 跨会话共享卸载内容（每个会话有自己的卸载目录）

## Decisions — 决策

### 决策 1：卸载器位于 services/context/，而非 engine/

**选择：** 创建 `src/hecate/services/context/offloader.py`。LLMWorker 通过 `execution_context["environment"]`（AgentEnvironment）接收它，并内联构造卸载器，或者通过 `execution_context["context_offloader"]` 接收预构建的卸载器。

**理由：** 引擎层零外部依赖（AGENTS.md："engine/ → Zero external deps"）。`AgentEnvironment` 定义在 `services/environment/environment.py` 中。将卸载器放在 engine/ 中需要从 services/ 导入，违反分层原则。

**考虑的替代方案：**
- *将卸载器放在 engine/*：拒绝 — 分层违规，引擎无法导入 services。
- *在 engine/ports.py 中定义 Offloader ABC*：一个类这样做过度设计了；增加了当前不需要的扩展点。

**最终方案：** LLMWorker 消费 `execution_context["environment"]`（一个 `AgentEnvironment` 或 None），并调用 `services/context/offloader.py` 中的辅助函数。由于 `engine/workers/llm_worker.py` 已经导入了 `hecate.engine.context`，但我们需要 services 层访问权限，卸载器通过 execution_context 作为可调用对象/实例注入，或者我们传递环境并让 LLMWorker 调用一个小辅助函数。最干净的方式：**通过 execution_context 传递环境；卸载器逻辑是 services/ 中的纯函数，LLMWorker 仅在 environment 存在时调用。**

等等 — 引擎无法从 services 导入。所以卸载器必须通过 execution_context **传入**，而不是导入。决策：`execution_context["context_offloader"]` 持有一个 `ContextOffloader` 实例（由任何编排 PregelRuntime 的东西构建 — 通常是 services 层）。LLMWorker 在存在时调用 `offloader.offload(messages)`。不需要 engine→services 的导入。

### 决策 2：卸载在压缩之前、选择之后触发

**选择：** 管道变为 5 步：
```
truncation → estimation → selection → offload → compress (last resort)
```

**理由：**
- 在 selection 之后，我们确切知道哪些消息正在被丢弃（未选中的消息）。
- 将这些特定消息卸载到文件，替换为引用桩。
- 在 [stub + selected] 列表上重新计算 tokens。如果仍然超出预算，则进行压缩。
- 这意味着压缩（真正的删除）只会在即使 stub 也放不下时发生 — 极为罕见。

**考虑的替代方案：** 在选择之前卸载（先卸载所有内容，然后从剩余部分中选择）。拒绝 — 我们会卸载本应被选中的消息，浪费存储空间并丢失本可以保持在行内的上下文。

### 决策 3：卸载的消息存储为 JSON，而非 Markdown

**选择：** 将丢弃的消息序列化为 JSON 保存到 `memory/sessions/{session_id}/offloaded_{timestamp}.json`。

**理由：**
- JSON 保留消息结构（role、content、tool_calls、tool_call_id）。
- Markdown 会丢失 tool_calls 结构，需要解析器才能恢复。
- Agent 的 `read_file` 返回字节；JSON 在检索时易于解释。
- 活动上下文中的引用桩格式化为 Markdown，便于 LLM 阅读。

**考虑的替代方案：**
- *Markdown*：对 tool 消息有损。拒绝。
- *MessagePack*：增加依赖。JSON 通用且可调试。
- *每个消息一个文件*：文件太多；难以批量检索。

### 决策 4：引用桩格式

**选择：** 将卸载的块替换为单个 system 角色的消息：
```
[Earlier conversation (messages 1-{N}) offloaded to {path}.
 Topics: {auto_summary}.
 Use read_file("{path}") to retrieve the full content.]
```

其中 `{auto_summary}` 是廉价的启发式摘要（每条用户消息的前 200 个字符，总计截断到 500 个字符）— 不是 LLM 摘要，以保持卸载延迟接近零。

**理由：**
- System 角色避免污染 user/assistant 轮次。
- 主题提示帮助 LLM 决定是否需要检索。
- 显式的 `read_file` 指令告诉 LLM 如何恢复。
- 启发式摘要避免了额外的 LLM 调用（延迟 + 成本）。

**考虑的替代方案：** 无摘要，仅一个指针。拒绝 — LLM 没有信号来判断检索是否值得。

### 决策 5：每次管道调用生成一个卸载文件，不累积

**选择：** 每次管道运行并触发卸载时，写入一个新的带时间戳的文件。不与之前的卸载内容合并。

**理由：**
- 实现更简单 — 无需对现有卸载文件进行读-改-写操作。
- 避免跨并发超步的竞态条件。
- 每次卸载是当时被丢弃内容的快照。
- 缺点：多个文件在长时间会话中累积。可接受 — Agent 可以读取其中任何一个，未来的清理任务（Session GC agent 13.9b）可以清理旧的卸载文件。

**考虑的替代方案：** 单个滚动文件，追加新的卸载内容。拒绝 — 并发超步写入会损坏它。

### 决策 6：配置设置

**选择：**
- `CONTEXT_OFFLOAD_ENABLED: bool = True` — 全局开关
- `CONTEXT_OFFLOAD_THRESHOLD_TOKENS: int = 6000` — 仅当溢出 ≥ 此阈值时才卸载

**理由：**
- 阈值防止为微小溢出（例如，超出预算 50 tokens → 不值得一次文件写入）进行卸载。
- 6000 默认值 ≈ 1500 行文本 — 值得保存的有意义的块。
- 全局开关让运维人员在存储受限的环境中禁用卸载。

### 决策 7：通过 execution_context 可选性实现向后兼容

**选择：** 如果 `execution_context` 缺少 `"context_offloader"` 或卸载器没有 `AgentEnvironment`，管道跳过卸载并完全按当前方式继续压缩。

**理由：** 零回归风险。现有的测试、没有环境的部署以及 ConversationService 路径不受影响。

## Risks / Trade-offs — 风险 / 权衡

- **[存储增长]** 每个长会话在 `memory/sessions/{session_id}/` 下累积卸载文件。→ 缓解：Session GC agent（13.9b）已经扫描孤立数据；卸载文件自然地属于其范围。可配置阈值限制频率。
- **[LLM 可能不检索]** Agent 可能忽略卸载桩并继续，没有早期上下文，降低质量。→ 缓解：桩中的主题提示给 LLM 提供信号。未来的增强：注入更强的系统提示提醒。
- **[卸载延迟]** 在每次超出预算的管道调用时向环境写入 JSON 文件会增加 I/O。→ 缓解：卸载仅在选择丢弃消息且溢出 ≥ 阈值时触发。对于 LocalEnvironment，文件写入是亚毫秒级的。对于 DockerEnvironment，基于 tar 的写入较慢，但仅在真正长时间对话时触发。
- **[无语义搜索]** Agent 必须知道主题才能决定是否检索。没有对卸载内容的向量搜索。→ 此变更接受此权衡。未来：将卸载的 JSON 输入 L4 知识记忆进行语义检索。
- **[桩仍占用 tokens]** 引用桩消耗部分预算（约 100 tokens）。如果预算非常紧张，桩 + 选中的消息可能仍然超出预算，强制进行压缩。→ 缓解：桩限制在 500 字符；作为最后手段的压缩被保留。
- **[execution_context 契约变更]** 添加 `"context_offloader"` 键是增量的。现有的 execution_context 消费者不受影响。→ 缓解：在 spec 中记录；键是可选的。

## Migration Plan — 迁移计划

无需迁移。这纯粹是增量添加：
1. 部署新代码，`CONTEXT_OFFLOAD_ENABLED=true`（默认）。
2. 在 PregelRuntime 构建时，当 `AgentEnvironment` 可用时注入 `ContextOffloader`。
3. 没有环境的现有部署自动回退到仅压缩路径。

**回滚：** 设置 `CONTEXT_OFFLOAD_ENABLED=false`。管道完全跳过卸载步骤。

## Open Questions — 开放问题

无 — 所有设计决策已解决。实现过程中的开放问题将在 tasks.md 中记录。
