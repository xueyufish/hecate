## Why — 为什么

当对话上下文超过 token 预算时，LLMWorker 管道目前调用 `InMemoryContextEngine.compress()`，它简单地丢弃最旧的消息（`messages[-max_messages:]`）。这导致**不可逆的信息丢失**：早期的用户需求、大型工具结果和推理链被永久丢弃，LLM 无法恢复它们。现有的四层记忆系统（L1 工作 / L2 会话 / L3 用户 / L4 知识）处理*提取的事实*的持久化，但不保留被引擎层上下文管道过滤掉的原始对话轮次。

我们现在拥有了 `AgentEnvironment`（1.3.15），提供 `write_file`/`read_file`/`exec_shell` 和每个 Agent 的持久存储。这解锁了一个更优的策略：不再删除溢出的上下文，而是**将其卸载到环境文件系统**，让 Agent 通过 `read_file` 按需检索 — 匹配 AgentScope（Offloader 协议）、Claude Code（基于文件的压缩）和 Amazon Bedrock AgentCore（`/mnt/workspace` 会话存储）使用的模式。

## What Changes — 变更内容

- **新增**：`services/context/offloader.py` 中的 `ContextOffloader` 类 — 将溢出的消息序列化到 AgentEnvironment 文件系统（`memory/sessions/{session_id}/offloaded_{timestamp}.json`），返回带有简短摘要和 `read_file` 检索提示的紧凑引用消息。
- **修改**：`LLMWorker._apply_context_pipeline()` — 在消息选择和压缩之间插入一个**卸载步骤**。首先尝试卸载；只有在卸载不可用或卸载后预算仍然超出时，才使用压缩（删除）作为最后手段。
- **修改**：PregelRuntime execution_context — 注入 Agent 的 `AgentEnvironment`（当可用时），以便 LLMWorker 可以访问环境存储。通过 `execution_context["environment"]` 传播。
- **新增**：配置设置 `CONTEXT_OFFLOAD_THRESHOLD_TOKENS`（默认 6000）— 触发卸载的最小 token 溢出量。防止微不足道的小溢出触发卸载。
- **新增**：配置设置 `CONTEXT_OFFLOAD_ENABLED`（默认 `true`）— 全局开关。
- **向后兼容**：当 execution_context 中没有 `AgentEnvironment` 时，管道回退到现有的 `InMemoryContextEngine.compress()` 行为。无环境 → 不卸载 → 无回归。

## Capabilities — 能力

### 新能力
- `context-offloading`：一个涉及 ContextOffloader 组件的新能力 — 其契约、存储布局、消息格式和检索语义。这是上下文管理的子能力，范围限定在卸载机制本身。

### 修改的能力
- `context-engine`：`LLMWorker` 上下文管道行为发生变化 — 在压缩之前插入一个新的卸载步骤，管道现在可以选择从 `execution_context` 消费 `AgentEnvironment`。ContextEngine ABC 本身不变；修改的是 `LLMWorker` 如何编排 context_engine + environment。

## Impact — 影响

- **代码**：
  - `src/hecate/services/context/offloader.py`（新增）— ContextOffloader 类
  - `src/hecate/engine/workers/llm_worker.py`（修改）— `_apply_context_pipeline()` 增加卸载步骤
  - `src/hecate/engine/pregel.py`（修改）— execution_context 在可用时注入 environment
  - `src/hecate/core/config.py`（修改）— 两个新设置
- **API**：无外部 API 变更。内部 execution_context 契约增加可选的 `"environment"` 键。
- **依赖**：无新的外部依赖。复用现有的 `AgentEnvironment.write_file()` / `read_file()`。
- **存储**：卸载的上下文存储在 `{WORKSPACE_ROOT}/{agent_id}/memory/sessions/{session_id}/offloaded_*.json`（LocalEnvironment）或 `/env/memory/sessions/...`（DockerEnvironment）。
- **测试**：ContextOffloader 的新测试套件和修改后的 LLMWorker 管道行为测试。
