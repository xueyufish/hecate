# Design — 8.20 执行回放与调试面板

## Context

1.3.19（ADR-030）交付后，事件日志已是携带值的执行状态事实源：`CHANNEL_WRITE {channel, value, log_schema_version}`、WAL 排序、`STEP_END` 提交点、子图嵌套 session（`SUBGRAPH_START/END {child_session_id}`）。ADR-030 为 8.20 预留的接缝合同是**纯消费者**：读 `get_events` + OTel `TraceModel` JOIN，零 schema 变更。

现有数据面（本变更的全部输入）：

- `Event`（`engine/eventstore.py`）：`session_id, superstep, event_type, node_id, timestamp, payload, trace_id, version`。
- `fold_session`（`engine/logfold.py`）：用与 live 相同的 `ChannelBehavior.write` 从日志重建 channel 状态；`derive_messages()` 投影模型可见消息。
- `TraceModel`（`models/trace.py`）：span 树 + timing + usage，`GET /management/traces` 已存在但 web 前端未消费。
- Web 已有 `@xyflow/react`（workflow canvas）与 Recharts。

**当前执行路由的覆盖现实**（`api/v1/chat.py`）：agent 配置 tools 的聊天走 Path A（`_chat_with_tools` 直连循环，无事件）；带 kb_ids/开场白/建议的增强聊天走 Path B（`WorkflowExecutionService` → Pregel，有事件）；纯文本走 Path C（透传，无事件）；Web 端 `ConversationService` 为直连循环（无事件）；workflow/canvas 执行、调度、子图走 Pregel（有事件）。因此 Phase 1 回放的实际主角是 workflow/graph 执行与增强聊天。

两个已确认的地基缺陷（本变更前置修复）：

1. **trace correlation 半覆盖**：pregel 发射的事件带 `trace_id`，但 `llm_worker` / `tool_worker` 直接 append 的 `LLM_REQUEST/LLM_RESPONSE/TOOL_CALL/TOOL_RESULT` 未传（`execution_context["trace_id"]` 已在手边未用）。
2. **identity 退化**：trace_id 取自 OTel span context；noop tracer 下为全零，所有 invoke 共享同一退化值，分片失效。

## Goals / Non-Goals

**Goals:**

- 词汇模型落地：`session → trace → event` 三层，"run" 退役（API 路径、UI 文案、文档措辞全部不用 run）。
- Pregel 路径执行的完整回放：trace 分段时间线 + channel 变更 + LLM/tool 正文 + guardrail 推导 + 子图跳转。
- time-travel：fold-to-version + `derive_messages`，只读。
- 事件与 trace correlation 的完备性（前置补齐）。

**Non-Goals:**

- fork/重跑（写路径）——回放严格只读。
- graph version binding（Phase 2 / P5）。
- turn 精确分段——等 1.3.4 `TURN_START/TURN_END`，落地后从 trace 分段升级，API 形状不变。
- path A/C 收敛为 engine-internal subgraph（已有 roadmap 债务登记，属 1.3.5i E3 配套）。
- 回放数据的实时流式推送（回放面向已完成/进行中执行的快照读取；不做 WebSocket 订阅）。

## Decisions

### D1. 词汇与概念模型：`session → trace → event`

（调研结论，2026-08-16，20 平台对比）业界观测语境收敛词是 **trace**（LangSmith/LangFuse/IBM/华为），对话语境是 **turn**（dsh/Codex/Agentforce）；裸 "run" 无一家作为一等概念。Hecate 的数据模型已是 LangFuse 形状（Session → trace_id 分片 → Events），`Event.trace_id` 来自每次 invoke 的 OTel span context——"一次执行"的边界已隐式存在，无需发明实体。

- 备选否决：自造 `RUN_START/RUN_END` 事件——与 1.3.4 的 TURN 边界语义地盘冲突，重复造边界。
- 精度备注：trace ≈ 一次 invoke；interrupt→resume 的一次 turn 横跨 2 个 trace。Phase 1 接受此粒度（每次 invoke 本身是完整 Pregel 执行），1.3.4 落地后升级。
- UI 中文文案统一"执行"；API 路径不含 run 字样。

### D2. 前置补齐：execution identity 与 trace_id 透传

- `PregelRuntime.invoke()` 入口建立 execution identity：OTel span context 有效（非零）时用之，否则生成 `uuid4().hex`（32 字符，形态与 OTel trace_id 一致）。identity 经 `execution_context["trace_id"]` 下传。
- `llm_worker` / `tool_worker` 的 4 处 Event append 补 `trace_id=execution_context.get("trace_id")`。
- 历史数据不迁移：既有无 trace_id / 全零 trace_id 的事件在回放中归入"未分段"区（见 D4）。

### D3. 回放 API 形状（management 平台，挂 session 域）

```
GET  /management/sessions/{session_id}/replay            # 时间线（分页）
     ?from_version=<int>&limit=<int>
GET  /management/sessions/{session_id}/replay/state      # time-travel 检查点
     ?at_version=<int>
```

- 时间线响应：`{ traces: [ {trace_id, events: [...], timing?, usage?} ], unattributed: [...], next_cursor }`；每条事件含 `event_type/superstep/node_id/timestamp/version/payload_summary`。
- **payload 裁剪**：列表响应对大 payload（`LLM_REQUEST.messages`、`CHANNEL_WRITE.value`）只返回摘要（长度 + 前 `REPLAY_PAYLOAD_PREVIEW_CHARS`（=200）字符预览，非字符串值先 JSON 序列化再截断），并带 `payload_truncated: bool` 标志；全量明细通过同一端点的 `?detail=full` 查询参数返回（UI 取单条时用 `from_version=<v>&limit=1&detail=full`），详情模式不设上限——调试语义需要全量（LLM_REQUEST 冻结消息体量大是特性）。仓库截断先例：workflow 测试面板 1000 字符、tool_result_limit 2000，本处取更小值因为时间线是批量响应。
- timing/usage 合并：按 trace_id 查 `TraceModel`，缺失时降级（无 timing 字段），不阻塞回放。
- **分页**：`limit` 默认 100、`Query(ge=1, le=500)`（对齐 traces API 的 Query 校验风格；默认值依据：一次 chat 轮 ≈ 2–6 superstep × 10–30 事件 ≈ 30–150 事件，100 一页覆盖多数轮次；上限 500 = 单会话 10k 事件告警阈值的 1/20）；游标为 version（`from_version`），跨页顺序天然稳定。

分层约束：assembler 位于 `services/replay/`，可 import engine（`fold_session`）与 models；api 层只调 service——符合 api 不直接 import engine 的分层规则。

### D4. trace 分段规则

按事件的 `trace_id` 字段值分片（保序）：同值连续归一段，段首 version 即段起点。`trace_id` 为 None 或全零的事件归入 `unattributed` 区，置于时间线末尾，不静默丢弃（历史数据兼容）。段顺序按首个事件 version 升序。

### D5. 正文关联规则（LLM_RESPONSE / TOOL_RESULT → messages 写入）

`LLM_RESPONSE` / `TOOL_RESULT` 的 payload 只有长度。assembler 将其与**同一 node 执行窗内**（`NODE_START` 到对应 `NODE_END` 之间）、紧随其后写入 `messages` channel 的 `CHANNEL_WRITE` 关联：

- LLM 节点：LLM_RESPONSE 后该节点的 messages 写入 = assistant 消息（正文）。
- Tool 节点：TOOL_RESULT 后的 messages 写入 = tool role 消息（含 is_error）。
- 关联是纯读侧投影，与 fold 语义一致（同一 `ChannelBehavior.write` 投影出的消息列表）。

### D6. guardrail 推导

只读投影：扫描 messages channel 写入，`is_error=True` 的 tool role 消息且内容匹配拦截标记前缀（`Tool blocked: `、`Tool denied by access policy`）→ 生成 guardrail 拦截项（原因文本 + 位置）。E3 stage 事件落地后切换数据源为事件，API 形状不变（spec 已约定升级路径）。

### D7. time-travel 实现

- 只读 fold：`ChannelManager` 临时实例 + `fold_session(events[:N])`，然后 `derive_messages()`。
- 提交点回退：预扫描 `STEP_END` 的 version 集合；`at_version` 非提交点时回退到 ≤N 的最大提交点并在响应标注 `effective_version`。
- `NonReplayablePrefix` → 422（含 `stopped_at_version`）。
- 性能：每次检查点全量 fold，O(日志长度)。Phase 1 接受（单次人工调试操作，会话级阈值 10k 事件内）；物化缓存留给未来（复用 SessionStateMaterializer 模式，不在本变更）。

### D8. DAG 拓扑与步进

- 拓扑来源：session 关联的 agent/workflow **当前**图定义（`SessionModel.agent_id` → graph DSL），UI 标注"拓扑取自当前定义版本"。
- 步进：时间线选中 superstep → 高亮该步 `NODE_START→NODE_END` 节点 + `CHANNEL_WRITE` 目标 channel 边。
- 未识别节点（node_id 不在当前定义）：占位节点呈现，不中断。
- 多 agent 子图：`SUBGRAPH_START {child_session_id}` 事件渲染为跳转链接 → 子 session 回放视图（同能力递归）。

### D9. UI 结构

- `web/src/app/(dashboard)/ops-center/conversations/[id]/page.tsx`：会话详情 + tabs（消息 / 执行回放）。
- 空日志会话（`log_version == 0`）：不渲染回放 tab。会话**详情** API 暴露 `log_version: int`（`get_version` 结果，0 = 无日志；前端 `log_version > 0` 判定）——int 而非布尔，因为它与回放 API 的 `from_version` 游标语义对齐且顺带暴露日志规模。列表 API 不暴露该字段（避免逐会话 `get_version` 的 N+1 查询；tab 只在详情页渲染）。
- 组件：`web/src/components/replay/`——`trace-segments`（分段条）、`timeline`（事件流）、`dag-replay`（React Flow 高亮）、`event-detail`（明细抽屉）、`state-inspector`（time-travel 结果：状态摘要 + 消息列表）。
- 覆盖横幅：回放 tab 顶部持久展示 "path A/C calls not in log"。

### D10. 租户隔离

- 回放端点：按 session 的归属（conversation/agent → workspace/org）校验，越权 404，复用 management 平台既有 scoping 模式。
- `GET /management/traces` 补租户过滤：`TraceModel` 无 org 字段，过滤经 `session_id`/`agent_id` JOIN 作用域表实现；无 session/agent 关联的孤儿 trace 仅平台管理员角色可见。

## Risks / Trade-offs

- [大日志响应体膨胀（10k 事件 × 携值 payload）] → version 游标分页 + payload 摘要裁剪（D3）；明细按需加载。
- [time-travel 全量 fold 的延迟] → Phase 1 接受单次交互延迟；明确非流式；未来物化缓存。
- [traces JOIN 数据不全（span 异步队列丢帧/未启用 tracing）] → timing/usage 缺失降级呈现，回放主数据不依赖 traces。
- [拓扑与日志不一致（执行后图被编辑）] → UI 标注 + 未识别节点占位（D8）；根治属 Phase 2 version binding。
- [guardrail 推导依赖消息文案前缀，脆弱] → spec 已锁定 E3 升级路径；推导仅作展示维度，非审计依据。
- [traces 租户过滤的 JOIN 成本] → 过滤走索引列（session_id/agent_id），列表页 limit 封顶。

## Migration Plan

无数据库 schema 变更、无事件格式变更（trace_id 透传为既有字段的使用补齐）。部署即生效；回滚 = 还原代码（新端点消失，无残留状态）。存量事件（无 trace_id 或 `log_schema_version<2`）按 unattributed / 422 路径处理，不需要迁移。
