# Tasks — 8.20 执行回放与调试面板

## 1. Engine 前置补齐（execution identity + trace correlation）

- [x] 1.1 `PregelRuntime.invoke()` 入口建立 execution identity：OTel span context 有效（非零）时采用，否则生成 `uuid4().hex`；经 `execution_context["trace_id"]` 下传（`engine/pregel.py`）
- [x] 1.2 `llm_worker` 的 `LLM_REQUEST` / `LLM_RESPONSE` 两处 Event append 补传 `trace_id=execution_context.get("trace_id")`（`engine/workers/llm_worker.py`）
- [x] 1.3 `tool_worker` 的 `TOOL_CALL` / `TOOL_RESULT` 两处 Event append 补传 `trace_id`（`engine/workers/tool_worker.py`）
- [x] 1.4 Engine 单测：identity 分片（同 invoke 事件同 id、连续 invoke 不同 id、resume 产生新 id）、noop tracer 场景不退化、worker 事件带 trace_id、`trace_id=None` 不影响事件记录

## 2. Services 层回放组装器

- [x] 2.1 新建 `services/replay/assembler.py`：按 `trace_id` 分片（None/全零归 `unattributed`）、version 升序、trace 分段元数据（段首 version、事件数）
- [x] 2.2 实现正文关联（D5）：LLM_RESPONSE / TOOL_RESULT 与同一 NODE 执行窗内紧随的 messages `CHANNEL_WRITE` 关联，产出 assistant 正文 / tool 消息（含 is_error）
- [x] 2.3 实现 guardrail 推导（D6）：拦截标记前缀匹配 → 拦截项列表（原因 + 位置）
- [x] 2.4 实现 payload 摘要裁剪：大 payload（messages、channel value）截断为长度 + 预览，支持详情模式返回全量
- [x] 2.5 新建 `services/replay/state_inspector.py`：time-travel——预扫描 `STEP_END` 提交点集合，fold 到 `effective_version`，返回状态摘要 + `derive_messages()`；`NonReplayablePrefix` 映射 422 信息
- [x] 2.6 timing/usage 合并：按 trace_id 查 `TraceModel`（span 树 + usage），缺失时降级
- [x] 2.7 Services 单测：分片规则（含 unattributed）、正文关联、guardrail 推导、裁剪、提交点回退、不可回放前缀 422、traces 缺失降级（stub event store + 内存 TraceModel）

## 3. API 层

- [x] 3.1 新增 `GET /management/sessions/{session_id}/replay`（version 游标分页 + 详情模式），调用 assembler；session 不存在返回 404
- [x] 3.2 新增 `GET /management/sessions/{session_id}/replay/state?at_version=N`，调用 state inspector；租户越权 404
- [x] 3.3 会话详情 API 暴露 `log_version: int`（0 = 无日志；列表 API 不暴露），供前端决定是否渲染回放 tab
- [x] 3.4 `GET /management/traces` 与 `GET /management/traces/{trace_id}` 补租户过滤（经 session_id/agent_id JOIN 作用域表；孤儿 trace 仅平台管理员可见）
- [x] 3.5 API 测试（conftest `client` + `db_session`）：回放端点分页/404/租户越权、state 端点回退与 422、traces 租户过滤正反例

## 4. Web UI

- [x] 4.1 `api-client.ts` 增加回放相关类型与请求函数（timeline、state、traces）
- [x] 4.2 新建会话详情页 `ops-center/conversations/[id]/page.tsx`（消息 tab + 条件渲染"执行回放" tab，依据 `log_version`）
- [x] 4.3 `components/replay/trace-segments`：trace 分段条（含 unattributed 区），点击切换段
- [x] 4.4 `components/replay/timeline`：事件流（superstep 分组、事件类型图标、guardrail 拦截标注、覆盖范围横幅"path A/C calls not in log"）
- [x] 4.5 `components/replay/event-detail`：事件明细抽屉（payload 摘要/详情切换、LLM 正文、tool 消息、timing/usage）
- [x] 4.6 `components/replay/dag-replay`：React Flow DAG（当前定义拓扑 + 拓扑来源标注 + 未识别节点占位），随选中 superstep 高亮节点与 channel 写入；`SUBGRAPH_START` 渲染子 session 跳转链接
- [x] 4.7 `components/replay/state-inspector`：time-travel 结果（状态摘要 + 模型可见消息列表 + `effective_version` 标注）
- [x] 4.8 前端测试（vitest）：分段切换、空日志隐藏 tab、未识别节点占位渲染、time-travel 展示

## 5. 验证与文档

- [x] 5.1 全量验证：`ruff check src/hecate/ tests/` + `ruff format --check src/ tests/` + `mypy src/` + `python -m pytest tests/ -q` 全绿
- [x] 5.2 文档同步：roadmap 8.20 条目措辞（"Run Replay" → "Execution Replay（执行回放）"，Feature ID 不变）、`positioning.md` 差异化表述、`engine-design.md` 8.20 指向更新
- [ ] 5.3 端到端手工验证：跑一个带工具的 workflow 执行 → 会话详情打开回放 tab → 分段/时间线/DAG 高亮/time-travel/子图跳转全链路走通
