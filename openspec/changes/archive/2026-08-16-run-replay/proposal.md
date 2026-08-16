# 8.20 执行回放与调试面板（Execution Replay & Debug Dashboard）

## Why

1.3.19（Log-as-Truth，ADR-030）已把事件日志升级为携带值的执行状态事实源（`CHANNEL_WRITE {value}`、WAL 排序、`STEP_END` 提交点、子图嵌套 session），并在接缝登记表中为 8.20 预留了"纯消费者"合同：读 `get_events` + OTel `TraceModel` JOIN，零 schema 变更。但今天**没有任何读取侧产品消费这份日志**——排障仍需人工翻 events 表和零散的 trace 记录，1.3.19 的投资无法转化为调试效率。8.20 在 roadmap 中排序于 1.3.19 之后（Sprint 7），依赖已满足。

术语决策（2026-08-16 调研，20 个平台对比）：roadmap 原文中的 "runId" 是悬空借词——业界无一家把裸 "run" 作为一等 API 概念（总是 "workflow run"/"chat run" 复合形态，或被 trace/turn/invocation/task 替代）。观测语境的收敛词是 **trace**（LangSmith / LangFuse / IBM / 华为），对话语境是 **turn**（dsh / Codex / Agentforce）。Hecate 的数据模型已经天然是 LangFuse 形状（Session → trace_id 分片 → Events），因此本变更**退役 "run" 一词**：词汇表采用三层模型 `session`（多轮容器，对齐 OTel `gen_ai.conversation.id`）→ `trace`（一次执行，回放锚点）→ `event`（记录）。功能名在文档/UI 层面改为"执行回放"，feature ID 8.20 不变。

## What Changes

- **新增执行回放 API**（services + api/management）：按 session 读取事件日志，按 `trace_id` 分段组装回放时间线（superstep × channel 变更 × tool 调用 × LLM 请求/响应 × 错误/中断），并 JOIN traces 表补充 timing/usage；提供 fold-to-version 的 time-travel 检查点（"当时模型看到了什么"）。
- **新增 Web UI**：ops-center conversations 详情页内嵌"执行回放" tab——trace 分段时间线、DAG 步进高亮（React Flow，复用 workflow canvas 基建）、事件明细查看、time-travel 检查点、SUBGRAPH_START/END 子图链接跳转。
- **guardrail 结果以推导方式进入时间线**：Phase 1 从 messages channel 的合成消息标记（`"Tool blocked: ..."`）推导；1.3.5i E3 waterfall 中间件落地后升级为 stage 事件（无 API 变更）。
- **前置补齐（engine，纯增量）**：`llm_worker` / `tool_worker` 的 4 处 Event append 补传 `trace_id`（`execution_context` 已有）；invoke 入口显式生成 execution identity，不依赖 OTel SDK 必须配置（noop tracer 下 trace_id 退化为全零的问题）。
- **顺手修复**：`GET /management/traces` 与新的回放 API 补齐租户过滤（org scoping）。
- **覆盖范围如实呈现**：回放覆盖 = Pregel 路径（workflow/graph 执行、增强聊天）；UI 横幅标注 "path A/C calls not in log"；`get_version(session) == 0` 的会话不渲染回放 tab。
- **文档同步**：roadmap 8.20 条目与 positioning.md 的 "Run Replay" 措辞更新为 "Execution Replay（执行回放）"，不改变 feature ID。

## Capabilities

### New Capabilities

- `run-replay`: 执行回放与调试面板——回放时间线 API、time-travel 状态检查、会话详情回放 tab（含 DAG 可视化与子图跳转）、覆盖范围标注规则。

### Modified Capabilities

- `eventstore`: worker 发射的 `LLM_REQUEST` / `LLM_RESPONSE` / `TOOL_CALL` / `TOOL_RESULT` 事件补齐 trace correlation（从 `execution_context["trace_id"]` 透传）；invoke 入口的 execution identity 生成要求（OTel 无效时显式生成，不退化为共享零值）。
- `full-chain-tracing`: Trace query REST API 增加租户过滤要求（按 org/workspace scope），消除跨租户读取。

## Impact

- **后端**：`src/hecate/services/`（新 replay 组装服务）、`src/hecate/api/management/`（conversations 详情扩展、回放端点）、`src/hecate/engine/workers/llm_worker.py` 与 `tool_worker.py`（trace_id 补齐）、`src/hecate/api/management/traces.py`（租户过滤）。
- **前端**：`web/src/app/(dashboard)/ops-center/conversations/`（详情页 + 回放 tab）、`web/src/components/replay/`（新组件：时间线、DAG 高亮、事件明细、trace 分段条）。
- **API**：新增回放读取端点（只读，不引入写路径）；无 breaking change。
- **依赖**：无新第三方依赖（React Flow / Recharts 已在 web 依赖中）。
- **文档**：`docs/features/roadmap.md`（8.20 措辞）、`docs/design/positioning.md`（差异化表述）、`docs/design/engine-design.md`（8.20 指向更新）。
- **明确不做（Phase 1）**：fork/重跑（写路径）、graph version binding（Phase 2 / P5）、turn 精确分段（等 1.3.4 `TURN_START/TURN_END`，落地后从 trace 分段升级）、path A/C 收敛（已有 roadmap 债务登记）。
