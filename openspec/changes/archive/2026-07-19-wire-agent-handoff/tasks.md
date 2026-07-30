## 1. DSL & Compiler — DSL 与编译器

- [x] 1.1 在 `src/hecate/engine/graph-dsl.schema.json` 的 AGENT 节点配置中添加可选的 `handoff` 对象，包含字段 `context_mode`（枚举：`"inherited"` | `"isolated"` | `"summarized"`，默认 `"inherited"`）和 `description`（可选字符串）
- [x] 1.2 在 `src/hecate/engine/compiler.py` 中扩展 `GraphCompiler._validate_*`（或添加 `_validate_agent_handoff_config`）以通过 `GraphValidationError` 拒绝具有无效 `handoff.context_mode` 值的 AGENT 节点

## 2. PregelRuntime: populate handoff_targets in execution_context — 在 execution_context 中填充 handoff_targets

- [x] 2.1 在 `src/hecate/engine/pregel.py` 中定位 Worker 分发点（可能是 `_dispatch_node` 或 Worker 池调用）
- [x] 2.2 在分发前，如果节点类型是 AGENT 且编译图具有 `trigger` 为 `"handoff"` 或 `"dynamic_handoff"` 的出站边，构建 `{"node_id": str, "description": str}` 字典列表（优先从目标 AgentModel.description 解析描述，目标节点 `name` 为回退）
- [x] 2.3 将此列表作为 `execution_context["handoff_targets"]` 注入，然后传递给 `worker.execute(...)`
- [x] 2.4 对于非 AGENT 节点或没有 handoff 边的 AGENT 节点，保持 `handoff_targets` 不存在（不写入空键 — 保持契约干净）

## 3. AgentExecutionPort: inject handoff tool + detect handoff call — 注入 handoff 工具 + 检测 handoff 调用

- [x] 3.1 在 `src/hecate/services/orchestration/agent_execution_port.py` 中扩展 `agent_execute()` 以从 `context` 参数接受 `handoff_targets`（已通过 `AgentWorker._handle_direct_mode` 传递）
- [x] 3.2 如果 `handoff_targets` 非空，调用 `inject_handoff_tools(tools=[], compiled=None, node_id=...)` — 重构 `inject_handoff_tools` 以直接接受 `targets` 列表，而不需要 CompiledGraph（添加新的辅助函数签名，保留旧签名用于向后兼容）
- [x] 3.3 在 `src/hecate/services/orchestration/handoff.py` 中扩展 `build_handoff_tool_schema` 以接受可选的 `descriptions_by_target: dict[str, str] | None = None`；当提供时，将工具描述格式化为 `"Transfer to a specialist agent. Available targets:\n- {target}: {description}\n..."`
- [x] 3.4 在 LLM 响应后，扫描响应中名称是否为 `handoff_to_agent` 的工具调用（使用现有的 `is_handoff_tool_call()`）
- [x] 3.5 如果找到，针对请求的目标调用 `validate_handoff_target()`；在无效目标上，返回 `{"response": "<给 LLM 的错误消息，提示重试>", "usage": {...}}`，不包含 `handoff_to`
- [x] 3.6 如果有效，返回 `{"response": "", "handoff_to": "<target_node_id>", "usage": {...}, "_handoff_tool_call_id": "<原始 tool_call_id>", "_handoff_messages_snapshot": <handoff 时刻的消息>}` — 额外键供 Worker 在构建通道更新时使用

## 4. AgentWorker: translate handoff_to into Command(goto=...) — 将 handoff_to 转换为 Command(goto=...)

- [x] 4.1 在 `src/hecate/engine/workers/agent_worker.py` 的 `_handle_direct_mode` 中，在 `port.agent_execute(...)` 返回后，检查 `handoff_to` 键
- [x] 4.2 如果存在，从 `node_config` 读取 `handoff.context_mode`（默认 `"inherited"`）
- [x] 4.3 调用新的辅助函数 `build_handoff_channel_updates(...)`（在 `handoff.py` 中），生成正确配对的 `messages` 列表：
  - `inherited` → `[*messages_at_handoff, aimessage_with_tool_call, toolmessage_ack]`
  - `isolated` → `[system_note, aimessage_with_tool_call, toolmessage_ack]`
  - `summarized` → `[system_summary_message, aimessage_with_tool_call, toolmessage_ack]`
- [x] 4.4 返回 `WorkerResult(node_id=node_id, channel_updates={"messages": ...}, command=Command(goto=target))`
- [x] 4.5 如果 `handoff_to` 不存在，保持当前行为（将助手响应写入 `messages`）

## 5. handoff.py: message pairing, context_mode filter, summary — 消息配对、context_mode 过滤、摘要

- [x] 5.1 添加 `build_handoff_channel_updates(messages_snapshot, source_node_id, target_node_id, context_mode, tool_call_id, llm_tool_call_message) -> list[dict]`
- [x] 5.2 实现 `inherited` 模式：按原样传递 `messages_snapshot`，追加 AIMessage（以原始 `tool_call_id` 重新发出）和合成的 ToolMessage
- [x] 5.3 实现 `isolated` 模式：丢弃快照，发出 `{"role": "system", "content": f"Handed off from {source_node_id}"}` 消息加上 AIMessage + ToolMessage 对
- [x] 5.4 实现 `summarized` 模式：调用新的 `_summarize_messages(messages_snapshot, source_node_id) -> str` 辅助函数，使用配置的 LLM（通过端口注入）生成结构化摘要；包装为系统消息，然后追加 AIMessage + ToolMessage 对
- [x] 5.5 添加 tool_call_id 冲突处理：如果相同的 `tool_call_id` 在快照中出现两次，向第二次出现追加 `"-{uuid4_hex[:8]}"` 并记录 WARNING
- [x] 5.6 添加 `filter_messages_for_handoff(messages, context_mode, source_node_id, target_node_id) -> list[dict]` 作为公共入口点；记录三种模式

## 6. Tests: AgentExecutionPort

- [x] 6.1 测试 `handoff_targets` 非空时注入 handoff 工具（断言工具列表包含 `handoff_to_agent` 且 `enum` 正确）
- [x] 6.2 测试 `handoff_targets` 为空或不存在时不注入 handoff 工具
- [x] 6.3 测试 handoff 检测：模拟 LLM 返回对 `handoff_to_agent` 的工具调用；断言端口在结果字典中返回 `handoff_to`
- [x] 6.4 测试无效目标被拒绝：模拟 LLM 返回不在有效列表中的目标；断言返回错误响应（无 `handoff_to`）
- [x] 6.5 测试提供 `descriptions_by_target` 时工具模式中包含每个目标的描述
- [x] 6.6 测试 `descriptions_by_target` 为 None 时回退到通用描述

## 7. Tests: AgentWorker

- [x] 7.1 测试端口结果中的 `handoff_to` 产生 `WorkerResult(command=Command(goto=...))`
- [x] 7.2 测试 `inherited` context_mode：结果 `messages` 包含快照 + AIMessage + ToolMessage
- [x] 7.3 测试 `isolated` context_mode：结果 `messages` 仅包含系统通知 + AIMessage + ToolMessage
- [x] 7.4 测试 `summarized` context_mode：结果 `messages` 仅包含系统摘要 + AIMessage + ToolMessage（模拟摘要器）
- [x] 7.5 测试 AIMessage + ToolMessage 配对：`tool_call_id` 在两条消息上相同
- [x] 7.6 测试 tool_call_id 冲突在第二次出现时生成 UUID 后缀

## 8. Tests: PregelRuntime + end-to-end — PregelRuntime + 端到端测试

- [x] 8.1 测试 PregelRuntime 为具有静态 handoff 边的 AGENT 节点填充 `execution_context["handoff_targets"]`
- [x] 8.2 测试 PregelRuntime 为具有字典目标的 `dynamic_handoff` 边填充多个目标
- [x] 8.3 测试 PregelRuntime 省略非 AGENT 节点的 `handoff_targets`
- [x] 8.4 端到端：小型图 `triage_agent --(dynamic_handoff)--> {billing, tech}`；模拟 LLM 返回 `handoff_to_agent(target="tech")`；断言 PregelRuntime 在下一个超步骤执行 `tech` 节点

## 9. Tests: Compiler — 编译器测试

- [x] 9.1 测试具有有效 `handoff.context_mode` 值的 AGENT 节点成功编译
- [x] 9.2 测试具有无效 `handoff.context_mode`（例如 `"secure"`）的 AGENT 节点抛出 `GraphValidationError`
- [x] 9.3 测试没有 `handoff` 块的 AGENT 节点成功编译（向后兼容）

## 10. Documentation — 文档

- [x] 10.1 更新 `docs/design/engine-design.md`，添加 "Multi-Agent Handoff" 部分，涵盖执行路径、三种 context_mode 策略和工具配对契约
- [x] 10.2 向 `docs/design/engine-design.md` 添加一个简短的图 JSON 示例，并排显示静态 handoff 和动态 handoff

## 11. Verification — 验证

- [x] 11.1 运行 `ruff check src/hecate/ tests/` — 预期 0 错误
- [x] 11.2 运行 `ruff format --check src/ tests/` — 预期全部格式化
- [x] 11.3 运行 `mypy src/` — 预期 0 错误（来自可选依赖的现有误报已排除）
- [x] 11.4 运行 `python -m pytest tests/test_engine/test_handoff.py tests/test_services/test_orchestration/test_agent_execution_port.py tests/test_engine/test_pregel.py -v` — 全部通过
- [x] 11.5 运行完整测试套件 `python -m pytest tests/ -q` — 无回归
