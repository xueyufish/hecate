# Design: online-offline-evaluation-tasks (7.2c)

## Context

评估域现状（7.2/7.2a/7.2b 已交付）：16 个 built-in evaluator 经 `PluginRegistry` 注册（`ops/evaluation/engine.py:register_evaluators`）；`EvaluationEngine.run()` 在 HTTP 请求内同步执行 dataset × evaluators；7.2b 已验证了异步 job 生命周期模式（`ops/evaluation/synthesis/job.py`：`queued → running → completed/failed`，`asyncio.create_task` + 独立 `async_session_factory` session，同步阈值 20 条）。生产信号侧（已逐项核验代码）：OTel `HecateTraceSpanProcessor`（`packages/hecate-ops/span_processor.py`）把所有 span 落库到 `traces` 单表树（`type ∈ {trace, span, generation, tool, retrieval}`）；根 trace（`pregel.py` 创建 `session:{id}` span）的 `session_id` 由 `session.id` 属性写入，但 **`agent_id`、`input_data` 两列当前无任何写入方**，LLM generation span 的 `output_data` 仅含 `response_length/ttft_ms` 等指标、**不含回答文本**。turn 的 query/answer/tool_calls 实际落在 `events` 表（EventStore），且已有从 event log 重建消息的先例（`ops/ops_center/conversation_messages.py:project_conversation_messages`，返回 `{"role","content","created_at"}`）；`sessions.agent_id` 是既有可靠字段。`RuntimePort.agent_execute`（`runtime/ports.py:260`）提供非流式 run-to-completion 的 agent 调用入口，评估侧目前未使用。`ops/scheduling/manager.py` 已有 pg advisory lock + APScheduler 模式（本变更不接入 cron，仅复用 advisory lock 惯例）。

业界调研结论（AWS AgentCore Evaluations / Langfuse Evaluator+Rule / LangSmith Online Evaluations / Braintrust / W&B Weave / Salesforce Testing Center / Palantir AIP Evals / Pi）收敛出四点，直接塑造本设计：① 任务定义与执行分离且任务是一等资源；② 在线评估清一色 ingest-then-score（消费者模式），无一家在请求路径评分；③ 离线评估 invoking agent-under-test 是标配（AgentCore Dataset Runner、Salesforce 对话模拟）；④ 评分落库收敛为 target 类型化 score 行（Langfuse v4 已弃用 trace 级转 observation 级；AgentCore 结果按 OTel 约定 parented to span）。

## Goals / Non-Goals

**Goals:**

- 任务定义/执行分离：`EvaluationTaskModel` 一等资源 + 离线异步 run（复用 7.2b job 模式）
- 在线任务为常驻消费者：轮询 `traces` 表、确定性采样、异步评分，runtime 零改动
- `answer_source=agent`：经 `RuntimePort.agent_execute` 逐条调用被测 agent（通往 8.10/7.9 的门）
- target 类型化 score 存储，Trace/Model/Root-Agent/Tool 粒度可通过 score 行 + `traces` 属性聚合
- 多租户 workspace 隔离贯穿任务、run、score 三层

**Non-Goals:**

- 生产 trace → 数据集物化（7.2d）；本变更只产出 score，不写 `evaluation_items`
- 评估报表/看板与 group_by 聚合 API（7.2e）；v1 score 查询只做行级过滤
- cron/周期调度；历史 trace backfill（v1.5 候选）；质量回归告警通知（OE3 下半段）
- 在线 evaluator 版本锁定/clone（AgentCore 式）——v1 全部为 built-in evaluator（全局注册、无版本维度），以任务配置快照 evaluator 名单代替；待第三方 evaluator 出现再引入
- 在线评分内容脱敏开关（Claude Code 式 privacy redaction）——记为后续增强
- 多轮 session 级评估（`EvalInput.conversation_history` 已备，v1 采样单元为根 trace）

## Decisions

### D1. 任务模型：单表 `evaluation_tasks`，`task_type` 区分 offline/online，不建调度绑定

单表 + `task_type` 判别字段 + `config` JSON 承载类型特有配置（offline: `dataset_id`/`answer_source`/`threshold`/`baseline_run_id`/`tags`；online: `agent_id`/`sampling_rate`/`max_traces_per_cycle`）。evaluator 名单存 `evaluator_configs` JSON（沿用 `evaluation_runs` 同名列惯例）。在线任务状态 `active/disabled` 即 AgentCore 的 `executionStatus` 语义；`last_scanned_at` 水位线存任务行（新列），不用独立游标表。

**备选**：(a) 扩展 `ops/scheduling` 的 `ScheduledTaskModel` 加 evaluation executor——被否：任务 ≠ 调度，单次任务塞不进 cron 模型，且调研确认业界无 cron-eval 先例；(b) offline/online 分表——被否：CRUD/过滤/审计全量重复，`task_type` 判别足够。

### D2. 离线执行复用 `evaluation_runs`（加可空 `task_id`、`summary` 列），不新建 run 表

任务 run = `evaluation_runs` 行（`task_id` 非空），状态机沿用既有 `RunStatus`，异步执行照搬 7.2b job 模式（服务层新 `TaskRunJobService`，或直接内联于 task service——实现时按规模定，倾向独立文件 `ops/evaluation/tasks/runner.py`）。`summary` JSON 列存 pass/fail 汇总与 regressions（对齐 `evaluation-api` 既有 compare/regression 语义，实现复用其对比逻辑）。

**备选**：独立 `evaluation_task_runs` 表——被否：与 `evaluation_runs` 字段几乎重合，且 compare/regression API、runs 查询都建立在 run 表上，分表意味着两套查询与两套对比实现。既有同步 `POST /api/evaluation/runs` 路径完全不动（`task_id=NULL`），向后兼容。

### D3. `answer_source=agent` 走 `RuntimePort.agent_execute`，逐条调用、失败隔离

`EvaluationEngine.run()` 的 per-item 循环内：`answer_source=agent` 且 item 无 `generated_answer` 时，取 item 所属任务配置里的 `agent_id`，构造 `messages=[{"role":"user","content": query}]`、`channel_snapshot={}` 调用 `agent_execute`；单条 agent 失败 → 该 item 记 `-1.0` error score，不中断整轮。`agent_id` 来源：任务 config 新增可选 `agent_id`（offline 任务也可绑被测 agent）；未配置时 agent 路径报校验错误。**不做**答案缓存/确定性重放（agent 非确定性，业界以配对对比与多迭代处理，超出 v1）。

### D4. 在线评分 = 独立 asyncio worker 消费 `traces` 表，pg advisory lock 防重

`ops/evaluation/tasks/online_worker.py`：单进程 asyncio 常驻循环（组合根启动，feature flag `evaluation.online_scoring.enabled` 默认关），每周期：
1. 遍历 `status=active && task_type=online` 的任务；
2. 水位线查询：`traces JOIN sessions ON sessions.id = traces.session_id WHERE traces.type='trace' AND traces.status='completed' AND traces.created_at > task.last_scanned_at AND sessions.agent_id = task.agent_id`（`traces.agent_id` 列当前无写入方，agent 归属过滤走 `sessions.agent_id` join；按 `max_traces_per_cycle` 截断）；
3. 采样：`hash(trace_id) % 10^6 < sampling_rate × 10^6`（确定性，重扫不翻转）；
4. trace → `EvalInput`：以 trace `start_time/end_time` 对 `EventStore.replay(session_id)` 开窗，窗内最后一条 user 消息为 query、最后一条 assistant 消息为 generated_answer、其余消息为 `conversation_history`，`TOOL_CALL` 事件聚合 `tool_calls`——**traces 行只决定"采什么、何时采"，评分内容一律取自事件流**；
5. 逐 evaluator 评分 → 写 `evaluation_task_scores`；单 evaluator 异常记 error score，单 trace 异常跳过；
6. 推进 `last_scanned_at` 水位线（按本批最大 `created_at`）。

多进程部署防重：worker 启动即取 pg advisory lock（`ops/scheduling/manager.py` 惯例），取不到则本进程跳过启动。**备选**：(a) OTel `SpanProcessor` 追加评分订阅——被否：把评估放进热路径回调线程，违反 ingest-then-score 业界共识与 runtime 自足性；(b) `SessionEndHook`——被否：ABC 已定义但生产无接线缝，需先改 runtime；(c) `TURN_END` 事件驱动——被否：事件在 EventStore 内，跨域读取 + 消费位点管理比 `traces` 时间戳游标复杂，v1 收益为零。三者均列为后续演进选项（尤其 backfill 场景 a/b 更合适）。

### D5. 评分落库：新表 `evaluation_task_scores`，不动 `evaluation_scores`

新表字段：`task_id`、`target_type`（v1 恒 `trace`，String 预留 `session/span/tool`）、`target_id`（→ `traces.id`）、`session_id`、`agent_id`（冗余自 trace，免 join 即可做 Root-Agent 粒度聚合）、`metric_name`、`value`、`reasoning`、`source`、`status`（`completed/error`）、workspace_id、时间戳。幂等：唯一索引 `(task_id, target_type, target_id, metric_name)`，写入 `ON CONFLICT DO NOTHING`。**不动** `evaluation_scores`（offline 专用，`run_id/item_id` 天然 target）与 `conversation_turn_scores`（8.9b 域内资产）。Model/Tool 粒度聚合 = 查询层 join `traces`（generation span 的 model、tool span 的 name），v1 不冗余进 score 行——待 7.2e 定义聚合查询时再加列或视图。

**备选**：扩 `evaluation_scores` 为多态（nullable run_id/item_id + target 列）——被否：污染既有 API 语义，全部查询需 `IS NULL` 分支；industry（Langfuse）也是独立 score 实体 + 多挂点。

### D6. API 归属与契约

全部新端点挂既有 `/api/evaluation` 前缀，新建 router `ops/api/evaluation_tasks.py`（注册进既有 ops API 聚合，避免 `evaluation.py` 膨胀）：任务 CRUD、`/{id}/enable|disable`、`/{id}/runs`（POST 202 / GET 列表）、`GET /api/evaluation/scores`。Schema 命名遵循 `XxxCreateSchema/UpdateSchema/ReadSchema`（`models/evaluation.py`）。`EvaluationRunReadSchema` 增量 `task_id`/`summary` 字段。

### D7. 启动接线与可观测性

组合根（`core/composition/wiring.py`）在 feature flag 开启时启动 worker 并注册关闭钩子；worker 周期日志：每任务每周期 scanned/sampled/scored/error 计数，任务行 `metrics` JSON 同步累计值（对齐 7.2b job `metrics` 惯例），供 7.2e 直接消费。

## Risks / Trade-offs

- [在线评分 LLM 成本失控] → `sampling_rate` 必填 + `max_traces_per_cycle` 硬顶 + 默认关闭的 feature flag + 任务级 disable 即停；花费上限/成本预估列 v1.5
- [worker 单点/重启丢周期] → advisory lock 只保证单消费者，不保证高可用；水位线按 `created_at` 推进，重启后从游标续扫，最多重复扫一个未提交批（幂等唯一键兜底）
- [EventStore 重放开销（长 session / 高频采样）] → 按 trace 时间窗截取重放范围 + 窗内消息条数上限，超限记日志并降级跳过该样本；未来可改用 `TURN_START/TURN_END` 事件版本号精确开窗
- [`traces.agent_id/input_data/output_data` 数据空缺是观测侧短板] → 本设计绕开（sessions join + 事件流取内容），runtime 零改动；补全 root span 的 `agent.id` 与 input/output 属性属 runtime 观测增强，列为后续独立事项，本变更不依赖
- [`answer_source=agent` 大 dataset 放大延迟与成本] → 每条一次 agent 调用是 O(n) LLM 开销；任务 config 保留 `tags` 过滤缩小范围，spec 层面已明确逐条隔离失败；并发控制沿用 engine 现有串行循环（v1 不做并发提升，风险记录在案）
- [`traces.created_at` 扫描缺索引导致慢查询] → 实现时核验 `traces` 现有索引，不足则在 migration 中补 `(type, status, created_at)` 组合索引
- [评测器在任务启用后被移出注册表（如 ragas 依赖缺失的启动抖动）] → enable 时校验 + 评分时降级为 error score（reasoning 注明），任务不停摆

## Migration Plan

当前生产暂无实际使用，不做历史数据兼容：

1. 单个 alembic migration：建 `evaluation_tasks`、`evaluation_task_scores`（含幂等唯一索引与查询索引），`evaluation_runs` 加 `task_id`、`summary` 两列。两列可空是模型自身语义（请求触发的 run 本就没有任务归属），不是兼容性妥协。
2. 部署：migration 与应用一并上线即可，无需分阶段灰度或新旧代码共存窗口；上线后按需开启 worker feature flag。
3. 回滚：关 flag 冻结在线评分；schema 可直接 drop 表/列，无数据保留负担——如需清理，评估域相关表可整体重置。

## Open Questions

（原第一条 `traces.input_data/output_data` 字段名问题已核验解决：两列当前均无写入，评分内容改从 session 事件流取得，结论已并入 Context 与 D4。）

- 在线任务的 session 级（多轮）评估：v1 采样单元为根 trace（单轮窗口）。**延后去向**：与 7.2d/7.2e 同批（roadmap Sprint 8 Opening Queue，批内顺序开放、无日期承诺）；**触发条件**：7.2d 低分样本回流需会话级上下文，或 7.2e 粒度聚合报表需要 session 级 rollup。内容来源已在本变更内解决（session 事件流），届时仅需拉长开窗，不推翻本变更决策。**归档动作**：`/opsx-archive` 时在 roadmap 的 7.2d/7.2e 条目补记该增强项，避免该延后项仅存于本变更归档目录。
