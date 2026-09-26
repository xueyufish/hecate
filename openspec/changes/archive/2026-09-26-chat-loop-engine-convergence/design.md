# Design: chat-loop-engine-convergence

## Context

探索确认的事实（详见会话探索记录）：

- **图已存在**：`build_chat_graph`（`studio/workflows/templates.py`）已含 `__start__ → llm → check_tools ─(has tool)→ tool_call → llm` 循环 + suggestions 分支。B1b 无需新建子图。
- **分叉点**：`chat.py:345` 附近 `if agent_tools:` → 直连循环（带 `assemble_guardrails` 完整 bundle）；否则 → 引擎。`use_enhanced`（kb/opening/suggestions）分支在直连分支之后，传 `tools=request.tools`——agent 工具丢失（存量 bug）。
- **引擎装配缺口**：`WorkflowExecutionService._create_composite_worker` 构造 ToolWorker 时传了 hooks/access_policy/approval_callback/tool_rules，**未传** `middleware_chains` / `denial_tracker`，且未接 `event_store`（ToolWorker 支持这些参数）。`WorkflowExecutionService.__init__` 也未暴露这些入口。
- **流式**：引擎路径已有 `StreamMode.MESSAGES` → chat.py `_stream_with_workflow` 映射（message/values 事件 → SSE chunk）。直连循环的语义是"中间迭代静默、只流式最终答案"。
- **B1a 接口**：`get_tool_receipt(event_store, session_id, execution_id)`、`should_auto_retry(classification, receipt_status)`、事件 payload 的 `execution_id/status/side_effect_class`。
- **MCP 对齐**：MCP `agent_chat` / `session_resume` 已走 `WorkflowExecutionService`——迁移后 HTTP 与 MCP 天然一致。
- **会话锁**：chat 入口已有 `hold_lock_over_stream`（流式锁绑定流生命周期）——与执行路径无关，迁移不动它。

## Goals / Non-Goals

**Goals:**

- 开关开启时有工具 agent 经引擎执行，事件/回执/审批/恢复语义与 MCP 一致。
- 引擎 ToolWorker 装配与直连路径 gating 等价。
- 流式"中间迭代静默"语义保持；恢复不重放已成功的写操作。

**Non-Goals:**

- 不删除直连循环（开关稳定后的独立决定，预计 -800 行）。
- 不把审批切到引擎 interrupt 的流式表达（保持同步问询；interrupt 流式语义另行演进）。
- 不动 MCP / workflow 入口（已走引擎）。
- 不做跨进程恢复调度主体（Temporal vs 持久化作业的二选一仍开放）。
- 不改 chat 图模板本身（循环已存在；除非实现中发现 check_tools 判定缺口）。

## Decisions

### D1: 开关在 chat.py 分叉处判定，默认 `false`

`CHAT_TOOL_LOOP_ENGINE_ENABLED: bool = False`。`if agent_tools:` 分支改为 `if agent_tools and not settings.CHAT_TOOL_LOOP_ENGINE_ENABLED:` → 直连；否则落 入引擎分支。关闭时字节级行为不变（回归基线）。备选（否决）：per-agent 灰度字段——需要迁移与管理面，全局开关先验证正确性。

### D2: bundle 经 WorkflowExecutionService 构造参数下传

`WorkflowExecutionService.__init__` 增加可选 `guardrail_bundle`（或平铺参数：`middleware_chains` / `denial_tracker` / `tool_event_store`），`_create_composite_worker` 原样传给 ToolWorker。chat.py 引擎分支把 `assemble_guardrails` 的 bundle 传入。命名避让：ToolWorker 的 `event_store` 参数是执行事件存储，与 chat.py 现有的 `event_store` 同源——直接复用会话的 event_store 实例。

### D3: 引擎分支统一承载 enhanced + tools 组合

开关开启时，`use_enhanced` 与工具路径合并为单一引擎分支：`build_chat_graph` 已支持 tools 参数；kb 检索经图内 KnowledgeWorker（现有）。直连分支仅在开关关闭时到达。这消除 enhanced 丢工具 bug，也让流式映射只有一份。

### D4: 流式过滤——SSE 映射层按迭代归属筛选

`_stream_with_workflow` 现映射 message/values。中间迭代的 LLM 文本需要静默：以图事件中的节点/轮次归属判定"最终答案"（check_tools 判定 no-tool 之后的 LLM 输出）。实现取向：优先用引擎事件里已有的轮次/迭代标记；若 MESSAGES 模式不携带归属信息，则退化为"在 chat.py 侧缓冲 message 块、以 values/finish 事件为界只放行最后一段"——语义等价（最终答案即最后一段连续文本）。**实现时先核实 StreamMode.MESSAGES 的事件形状再定**，两种取向都不改图模板。

### D5: 恢复不重放落在 ToolWorker 执行入口

`_execute_single_tool` 执行前（生成 `execution_id` 后）查 `get_tool_receipt`：`succeeded` → 跳过执行，回填结果消息（结果内容取回执记录的 `result_digest`——B1a 未存全文，**回填以占位摘要 + 注明为恢复回填**，避免伪造全文）；`unknown` → 返回错误消息 + 事件标记人工核对（复用 CHANNEL_WRITE_REJECTED 或新增 CUSTOM 事件——实现时定，倾向复用）。无回执 → 按 `should_auto_retry`（readonly/idempotent 自动执行，其余返回待人工核对错误）。B1a 的 TOOL_RESULT payload 需要小增补：记录 `result_digest`（结果哈希/长度已有 result_length，补 digest 以便回填校验）。

### D6: 一致性验收用事件日志断言

三入口一致性矩阵的断言对象是事件日志（TOOL_CALL/TOOL_RESULT 序列、APPROVAL 记录、finish 语义），不是 SSE 文本——传输层差异不参与一致性判定。测试基座：同一 stub LLM + stub 工具注册表，HTTP(TestClient)/MCP(ASGI)/execution_service 直调三路跑同一 agent。

## Risks / Trade-offs

- [流式归属判定复杂度] MESSAGES 模式若不携带迭代归属，缓冲过滤可能把多段合法输出并成一段 → Mitigation：D4 的两种取向都以"最终答案"为锚；实现前先写事件形状探针测试。
- [直连与引擎的细微行为差] denial_tracker 语义、middleware chain 的 BLOCK 行为在两条路径的历史实现可能有细节差 → Mitigation：bundle 等价任务（对齐装配）先行，用同一 guardrail 测试集双路径回归。
- [开关期双路径维护] 开关稳定前两条路径都要维护 → 已知代价，换取随时可回退；删除时点由用户决定。
- [回执回填的占位语义] 恢复回填的是摘要而非全文，下游 LLM 看到的恢复结果与实时执行略有差 → 注明"recovered"标记，语义诚实优先；全文缓存留作后续增强。

## Migration Plan

1. 合入默认关闭——零行为变化，CI 全绿即无感。
2. 测试环境开启开关跑回归集（一致性矩阵 + 流式形状 + 恢复不重放）。
3. 灰度开启观察引擎事件量与延迟；异常随时关闭回退。
4. 开关稳定后的直连删除、审批 interrupt 流式表达、跨进程恢复调度主体——各自另行立项。

## Open Questions

- ~~StreamMode.MESSAGES 的事件是否携带迭代/节点归属~~ **已解决（实现探针）**：MESSAGES 事件无归属标记，采用 D4 备选的变体——LLMWorker 通道状态门控（配置了 tools 且通道无 tool 结果 → 静默迭代不流式；有 tool 结果 → 正常流式）+ SSE 层空增量过滤。比缓冲方案更优：最终答案保持逐 token 实时流式。
- ~~人工核对的事件载体~~ **已解决（实现）**：恢复拒绝路径以工具结果消息（`[needs review]` 前缀，is_error）表达 + 日志 WARNING；专用事件类型留待恢复调度主体立项时统一设计。
- execution_id 确定性派生：实现中发现 uuid4 每次随机的 id 无法跨重放匹配回执——已改为 `uuid5(session_id, tool_call_id)` 确定性派生（tc_id 缺失时退化为随机，回执匹配对该调用不可用）。B1a 的"服务端稳定 ID"语义保持满足。
