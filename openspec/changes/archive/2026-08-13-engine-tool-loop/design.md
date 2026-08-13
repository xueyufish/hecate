# engine-tool-loop — Design

## Context

PregelRuntime 的 chat 路径在 `templates.py::build_chat_graph()` 中已经定义了一个完整的工具循环图：

```
[__start__] → [llm] → [check_tools] ──(has tool)──→ [tool_call] → [llm] (loop)
                                     │
                                     (no tool)
                                     ▼
                               [suggestions] → [__end__]
```

图结构、`_has_tool_call` 通道、`check_tools` 条件节点、`ToolWorker` 均已存在。但这条路径**从未真正工作过**，断点在 `LLMWorker`：

1. **tools 从未注入 LLM 节点**：`build_chat_graph()` 的 LLM 节点 config 只有 `model`/`system_prompt`/`channels`，没有 `tools`。虽然 `WorkflowExecutionService.execute()` 通过 `initial_input["_tools"] = tools` 把工具列表放进了 channel 快照，但 LLMWorker 从 `node_config.get("tools")` 读取（返回 `None`），从不读取 channel 快照中的 `_tools`。
2. **`llm_invoke` 接口无法携带结构化 tool_calls**：`EnginePort.llm_invoke` 签名是 `AsyncGenerator[str, None]`，只能 yield 文本 token。即使底层 `LLMService.chat_stream` 的 chunk dict 已包含 `tool_calls` 字段（LiteLLM delta 分片），`EnginePortAdapter.llm_invoke` 也只提取 `content` 字段透传，丢弃 tool_calls。
3. **placeholder 检测永不触发**：`llm_worker.py` 中 `if response_dict.get("tool_calls"):` 是占位符——`response_dict` 只构造了 `content`/`model`，永远没有 `tool_calls` 键，所以 `_has_tool_call` 永远不会被置为 True，check_tools 永远走 false 分支。

实验验证（2026-08-13，tutorial-test 会话）：Pregel chat 路径中 `port.llm_invoke` 实际收到 `config={"tools": null}`，确认断点存在。

## Goals / Non-Goals

**Goals:**
- 让 Pregel chat 路径的工具循环真正工作：LLM 收到工具定义 → LLM 返回 tool_calls → check_tools 路由 → ToolWorker 执行 → 结果回注消息流 → 循环直到无工具调用
- 保持 `llm_invoke` 的向后兼容性（`routing.py`、`task_allocator.py`、既有测试桩不受破坏）
- 流式与非流式两条路径都支持

**Non-Goals:**
- 不修改 `build_chat_graph()` 的图拓扑（循环边、节点类型、通道定义已正确）
- 不修改 `ToolWorker`（其解析逻辑已存在且被 three_layer 模式验证）
- 不处理 `routing.py` 中 `await engine_port.llm_invoke(prompt=...)` 与签名的既有不一致（独立问题）
- 不实现 ContextEngine 之外的新上下文特性

## Decisions

### Decision 1：新增可选方法 `llm_invoke_structured`，而非改变 `llm_invoke` 签名

**选择**：在 `EnginePort` 上新增**可选**方法：

```python
async def llm_invoke_structured(
    self,
    messages: list[dict],
    config: dict,
) -> AsyncGenerator[dict[str, Any], None]:
    # 默认实现：委托 llm_invoke 收集 token，返回 {"content": str, "tool_calls": None}
```

yield 的 dict 结构：`{"content": str | None, "tool_calls": list[dict] | None}`——流式期间 yield `{"content": token}` 增量块，流结束时 yield 一个带 `tool_calls` 的最终块（或通过 return 语义约定）。

**理由**：
- EnginePort 已有可选方法先例（`context_assemble`、`evidence_query`、`agent_execute`、`tool_execute_sandbox` 均为带默认实现的可选方法），这是仓库既定模式。
- 改变 `llm_invoke` 签名会破坏 `routing.py`（`await llm_invoke(...)`）、`task_allocator.py`（`async for token`）、以及大量测试桩（mock `AsyncGenerator[str, None]`）——风险大且无必要。
- LLMService.chat_stream 的 chunk 已含 tool_calls，adapter 只需累积 delta 分片并在流结束时输出完整 tool_calls。

**考虑的替代方案**：
- 改 `llm_invoke` 签名返回 `AsyncGenerator[str | dict, None]` —— 破坏所有现有调用方，拒绝。
- 通过 config 传回调收集 tool_calls —— 非 async 友好、绕过类型系统，拒绝。

### Decision 2：LLM 节点注入 tools，来源为 channel 快照的 `_tools`

**选择**：`build_chat_graph()` 的 LLM 节点 config 增加 `tools` 键；`WorkflowExecutionService.execute()` 在调用 `build_chat_graph` 时把 `tools` 参数传入节点 config。LLMWorker 从 `node_config.get("tools")` 读取（现有逻辑已支持，只需确保 config 里有值）。

**理由**：
- LLMWorker 已从 `node_config.get("tools")` 读取并传给 `_filter_tools` 和 `port.llm_invoke`，最小改动是让 config 真正带上 tools。
- `execute()` 已有 `tools` 参数（来自 AgentModel.tools 解析），直接透传即可，无需改动数据流。

**替代方案**：LLMWorker 改从 `channel_snapshot.get("_tools")` 读取——`execute()` 已把 tools 放进 `initial_input["_tools"]`，但该值最终是否进入 channel 快照需要验证；节点级 config 注入更显式、更符合现有 node_config 读取模式，拒绝。

### Decision 3：LLMWorker 工具调用检测——真实解析 + 流式累积

**选择**：LLMWorker 在**有 tools 时**调用 `port.llm_invoke_structured`（无 tools 时维持 `llm_invoke` 现状），从结构化响应解析 `tool_calls`，设置 `_has_tool_call` 通道，并把 assistant tool_calls 消息写入 messages 通道（供 ToolWorker 消费）。

- **非流式路径**（`execute`）：`async for chunk in port.llm_invoke_structured(...)` 累积 content；流结束后若有 `tool_calls`，构造 `{"role": "assistant", "content": ..., "tool_calls": [...]}` 消息，置 `updates["_has_tool_call"] = True`。
- **流式路径**（`execute_stream`）：yield 每个 content token（保持 SSE 兼容）；同时累积 tool_calls delta；流结束后若检测到 tool_calls，再 yield 一个携带 `_has_tool_call` 信号的最终结果（StreamMode.MESSAGES 下 token 与状态事件混合是既有模式）。

**理由**：与 three_layer 模式中 LLMWorker 的既有行为对齐（`_has_tool_call` 通道设计就是为工具循环准备的）；ToolWorker 已能消费 messages 通道中的 assistant tool_calls + tool 结果消息，无需改动。

### Decision 4：EnginePortAdapter 累积 LiteLLM delta tool_calls

**选择**：`EnginePortAdapter.llm_invoke_structured` 实现：遍历 `LLMService.chat_stream`，yield content 增量块；累积 `chunk["tool_calls"]` 的 delta 分片（按 `index` 合并：同一 index 的 `function.arguments` 追加）；流结束时 yield 最终块携带合并后的完整 tool_calls。

**理由**：LiteLLM 流式返回的 tool_calls 是 delta 分片（每 chunk 只有部分参数），必须按 index 累积合并才能得到可执行的完整调用。该逻辑放 adapter（服务层），引擎层不感知 LiteLLM 细节——符合分层约束（engine 不 import services）。

## Risks / Trade-offs

- **[Risk] `llm_invoke_structured` 默认实现语义模糊** → 默认实现显式委托 `llm_invoke` 收集 token，返回 `tool_calls=None`，并在 docstring 中说明"未覆盖此方法的 EnginePort 实现不支持结构化工具调用"，保证降级安全。
- **[Risk] 流式 tool_calls 的 SSE 兼容性** → 流式路径继续 yield `{"content": token}` 块，tool_calls 只在最终 WorkerResult 中携带，不改变既有 SSE chunk 形状（与 tutorial 验证中 `_stream_chat_with_tools` 的模式一致）。
- **[Risk] `_filter_tools` 可能过滤掉全部工具** → 属既有安全机制，保持不变；若过滤后为空则 LLM 收不到 tools，与现状一致，不回归。
- **[Risk] 测试桩需新增 `llm_invoke_structured` mock** → 默认实现保证未 mock 的桩自动降级（调 `llm_invoke`），既有测试不破坏。
- **[Trade-off] 接口新增而非改造** → 语义上有两个 LLM 入口，但符合仓库 EnginePort 可选方法既定模式，文档中说明各自适用场景。

## Migration Plan

1. 实现 `EnginePort.llm_invoke_structured`（默认实现）+ `EnginePortAdapter` 覆盖实现
2. `build_chat_graph()` 注入 tools + `WorkflowExecutionService` 透传
3. LLMWorker 两条路径接入结构化调用
4. 新增/更新测试（LLMWorker 单测、chat 图集成测试、回归确认）
5. 验证：`ruff check` + `ruff format --check` + `mypy` + `pytest` 全绿

回滚策略：该变更集中在 engine 层，无数据迁移；回滚即 revert 相关 commit，`llm_invoke` 未动，既有路径不受影响。

## Open Questions

- 流式路径下 `_has_tool_call` 信号的传递方式：是否需要给 SSE 客户端暴露工具调用事件，还是仅引擎内部使用？（初步结论：仅内部使用，SSE 形状不变，与 ConversationService 流式行为对齐）
- 是否需要让 `ToolWorker` 的 tool 结果消息在下一轮 LLM 调用前被 `_apply_context_pipeline` 正确处理？（需在实现时验证既有 ContextEngine 管道对 tool 消息的兼容性）