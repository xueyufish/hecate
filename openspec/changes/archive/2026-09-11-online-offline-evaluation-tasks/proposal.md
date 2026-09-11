# Proposal: online-offline-evaluation-tasks (7.2c)

## Why

评估能力目前只有"同步内联"形态：`POST /api/evaluation/runs` 在 HTTP 请求内同步跑完 dataset × evaluators，无法承载大规模回归集，也无法周期性/常态化运行；同时生产流量（`traces` 表，由 OTel span processor 落库）完全没有被自动评分利用。业界（AWS Bedrock AgentCore Evaluations、Langfuse Evaluator/Rule、LangSmith Online Evaluations、Salesforce Testing Center + Observability）已收敛出标准形态：**离线评估任务 = 持久化任务定义 + 异步有界执行；在线评估任务 = 常驻消费者按采样率异步评分生产 trace**。7.2c（feature-catalog `7.2c`，Sprint 8 Opening Queue）补齐这一层，是 7.2d（trace 回流）、7.2e（评估报表）、8.10（CI/CD 评估门禁）、7.9（Testing Center）的共同前置。

## What Changes

- **新增 EvaluationTask 任务定义实体**（`evaluation_tasks` 表）：`task_type`（`offline` | `online`）、offline 侧绑定 `dataset_id` + evaluator 列表 + `answer_source`（`manual` | `pipeline` | `agent`）+ 可选 `threshold`/`baseline_run_id`；online 侧绑定 `agent_id` 过滤器 + evaluator 列表 + `sampling_rate`；两者均为 workspace 隔离的一等 CRUD 资源。
- **离线任务异步执行**：触发运行返回 202 + run_id，复用 7.2b 已验证的 job 生命周期（`queued → running → completed/failed`，`asyncio.create_task` + 独立 session）；运行落 `evaluation_runs`（新增可空 `task_id`、`summary` 列）。`answer_source=agent` 时经 `RuntimePort.agent_execute` 逐条调用被测 agent 生成答案（当前引擎只对预置答案评分，兜底走裸 LLMService）。
- **在线评估任务（ingest-then-score）**：任务为常驻资源，状态 `enabled`/`disabled`；后台 worker 轮询 `traces` 表新增完成的根 trace（水位线游标），按确定性采样（hash(trace_id) × sampling_rate）+ agent 归属过滤（经 `sessions` 关联）选取样本，从该 session 的事件流构建 `EvalInput`（trace 行确定采样对象与时间窗），调用既有 16 个 evaluator 评分。**runtime 请求路径零改动**（业界无一例外采用消费者模式，不用请求路径 hook）。
- **新增 target 类型化评分表 `evaluation_task_scores`**：`target_type`（v1 为 `trace`，预留 `session`/`span`/`tool`）+ `target_id` + `session_id` + `task_id` + `metric_name`/`value`/`reasoning`/`source` + 幂等唯一键 `(task_id, target_type, target_id, metric_name)`；Trace/Model/Root-Agent/Tool 粒度聚合 = join `traces` 属性（agent_id、generation span 的 model、tool span），不是四套评估器。
- **新 API**：任务 CRUD、`POST /tasks/{id}/enable|disable`（online）、`POST /tasks/{id}/runs`（offline，202）、任务 run 列表、`GET /api/evaluation/scores` 评分查询（按 task/target/session 过滤分页）。
- **成本护栏（v1 最小集）**：`sampling_rate ∈ (0, 1]` 必填、单轮扫描 `max_traces_per_cycle` 上限、enable 时校验 evaluator 必须已注册。
- **不改动**：既有同步 `POST /api/evaluation/runs`、runs/compare、regression API 全部保持兼容（新列可空、新字段增量）。

**明确不在本变更范围**（记入 Non-goals，防止与兄弟特性互相渗透）：生产 trace → 数据集物化（7.2d）；评估报表/看板（7.2e）；质量回归告警通知（OE3 下半段）；cron/周期调度（业界无先例，重复触发 = 再次调 run 接口）；历史 trace 回填 backfill（v1.5 候选）；人工标注（7.4）；OE8 三维结构化。

## Capabilities

### New Capabilities

- `evaluation-tasks`: 评估任务定义（offline/online 两型）、离线任务异步运行生命周期与 agent 被测调用、在线任务 enable/disable 生命周期、生产 trace 采样与异步评分、target 类型化评分存储与查询 API。

### Modified Capabilities

- `evaluation-api`: Run 查询类需求增量——`evaluation_runs` 新增可空 `task_id` 与 `summary` 字段后，run 列表/详情响应包含 `task_id` 与 `summary`（metric_averages、passed/failed 汇总），并支持按 `task_id` 过滤；既有需求语义不变（向后兼容的字段增量）。

## Impact

- **数据模型/迁移**：`src/hecate/models/evaluation.py`（新增 `EvaluationTaskModel`、`EvaluationTaskScoreModel`；`EvaluationRunModel` 加 `task_id`、`summary`）+ 一个 alembic migration。
- **评估域代码**：`src/hecate/ops/evaluation/`（新增 `tasks/` 服务层：task service、offline runner、online worker、session 事件流→EvalInput builder）；`engine.py` 增加可选 answer_source=agent 路径与 summary 产出（不改既有默认行为）。
- **API**：`src/hecate/ops/api/evaluation.py` 或新 router 扩展（任务 CRUD / enable / disable / runs / scores 端点）。
- **组合根**：`core/composition/wiring.py` 启动 online evaluation worker（受 feature flag 门控，默认关闭）；worker 使用 pg advisory lock 防多进程重复消费（复用 `ops/scheduling/manager.py` 既有模式）。
- **依赖**：仅复用既有依赖（SQLAlchemy、PluginRegistry、RuntimePort），无新三方包。
- **下游铺路**：为 8.10（同步 run 返回 pass/fail 语义）、7.2d（已评分样本回流）、7.2e（score 行即报表数据源）提供底座。
