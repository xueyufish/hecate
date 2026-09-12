# Design: automated-trace-backflow (7.2d)

## Context

7.2c 交付的在线评估任务把生产 trace 的机分落在 `evaluation_task_scores`（target 类型化，per task/target/metric 幂等 upsert），但明确"只产出 score、不写 `evaluation_items`"（7.2c design 记录）。7.4 在 `AnnotationService.push_to_dataset`（`src/hecate/ops/evaluation/annotation/service.py`）建立了人工验证回流管道：completed 标注 item → `build_eval_input` 投影 → 自包含 `EvaluationItemModel` + `metadata_.annotation.trace_id` 幂等 + `tags` 溯源。本 change 在这条既有管道旁边补规则驱动的自动化路径。

关键既有设施（全部复用，不新造）：

- `build_eval_input(session_id, window_start, window_end, event_store)`（`ops/evaluation/tasks/trace_input.py`）——从 session 事件流按 trace 时间窗投影 `EvalInput`；`EvalInput` 已带 `conversation_history` / `retrieved_contexts` / `tool_calls` / `agent_id` / `session_id` 字段，多轮上下文"仅需拉长开窗"（7.2c design 延后记录原文）。
- `_dataset_trace_ids(dataset_id)`（annotation service）——按 `metadata_.annotation.trace_id` 扫 dataset 做去重。
- `traces` 表 + EventStore 事件回放——在线 worker 已经用同一套投影做实时评分。

回流由"规则层 + 人工层"双层构成；known-bad / 版本管理等兄弟话题不在本 change。

## Goals / Non-Goals

**Goals:**

- 规则一等对象：可持久化、可审计的"从哪个任务、按什么分数条件、物化到哪个 dataset、单批多少条"
- 显式触发批量物化，幂等可重入（与人工路径共享去重）
- 多轮对话上下文随 item 落库（承接 7.2c 延后项）
- 机分溯源完整可查（哪条规则、哪个任务、哪些分数把这条 trace 带进来的）

**Non-Goals:**

- 平台内置 cron / worker 内联钩子；触发编排交给既有 scheduled-tasks 能力（组合方式见 Decisions D2）
- 机分转写 `expected_answer`
- 源 trace retention pin（item 自包含，链接断裂仅影响下钻）
- 物化内容 DLP 扫描（决策见 D6）
- known-bad 豁免（7.3c）、dataset 命名版本（7.3b）、publish 门禁（7.3a）

## Decisions

### D1: 规则对象挂在"任务→数据集"边上，而非 dataset 或 task 的内嵌配置

新建 `evaluation_backflow_rules` 表（`name` / `task_id` / `dataset_id` / `filters` JSON / `limit` / `max_turns` nullable / workspace_id + BaseModel 审计列）。理由：一个任务可能向多个 dataset 供数（低分进回归集、高分进 golden 候选），一个 dataset 也可能从多个任务收数；内嵌配置（task.config 或 dataset.metadata）无法表达 N:N 且没有独立审计面。Alternative：挂在 dataset 上的规则数组——被否，因为校验 source task 的存在性要跨对象写，且"运行历史"没有自然挂点。

`filters` 形态：`[{"metric_name": str, "min_score"?: float, "max_score"?: float}]`，多条 AND；空列表在 schema 层拒绝（等价于全量，容易误触发大批量物化）。`limit` 默认 500，作为单批硬上限（成本护栏，对齐 7.2c `max_traces_per_cycle` 的设计直觉）。

### D2: 触发 = 显式 run API；平台不加 cron、不挂 worker 钩子

`POST /api/evaluation/backflow-rules/{id}/run` 是唯一触发面。三个 alternative 的取舍：

- **online worker 内联钩子**（评分后即时评估规则）：把 dataset 写路径耦合进评分主流程，且 worker 已用 advisory-lock 保证单写者，再叠加物化事务会拉长锁窗口——否，v1.5 再评估。
- **平台内置 cron**：7.2c 已有先例决策（周期调度不内置，重复触发 = 再次调 run 接口）——沿用。
- **复用 scheduled-tasks**：平台已有调度能力，用户把 run 端点配成计划任务即获得周期回流，零新基础设施——采用，作为文档化的组合路径。

### D3: 候选筛选直接查 `evaluation_task_scores`，按 score band 过滤 + 确定性排序

在线评分的幂等 upsert 保证了 per (task, trace, metric) 只有一行"最新分"，filter 即普通 SQL 范围条件（AND 组合、`metric_name` 等值），无需先聚合再筛。排序用 `scored_at` 升序（最早命中的先物化——多次 run 之间推进是确定性的，不会被新分数无限插队）。分数快照（命中的 metric/value/scored_at）在物化时随行取出写入 item metadata，避免二次查询。

`limit` 的语义：**限额计在"物化条数"上，skip 不占额度**——候选按 oldest-first 全量游走（分组查询无 SQL 截断），创建满 `limit` 即停。若在 SQL 层截断，已物化的旧样本会永久占据窗口、新匹配饿死，与"剩余匹配保持 eligible"矛盾；skip 均为内存集合查找或廉价探测，全量游走成本可控。

### D4: 幂等去重升级为"双路径共享"，物化写入 `metadata_.backflow.trace_id`

把 annotation service 的 `_dataset_trace_ids` 提升为共享 helper（backflow service 复用），一次扫描同时识别 `metadata_.annotation.trace_id` 与 `metadata_.backflow.trace_id`（JSON 列上做两键 OR 匹配；dataset 级去重集合在单次 run 内存中维护，行为对齐 7.4）。历史 annotation 物化行不动（`annotation.trace_id` 键保留），本 change 不做 metadata 键迁移——双键读取成本低，迁移收益为零。Alternative：统一新键 `source_trace_id` 并回填——被否，回填是全表 UPDATE，只省一个 OR 条件。

### D5: 物化映射——item 自包含，多轮历史进 `metadata_.backflow`

`EvalInput` → `EvaluationItemModel` 映射：

| EvalInput 字段 | 去向 | 说明 |
|---|---|---|
| `query` / `generated_answer` | `item.query` / `item.generated_answer` | 自包含拷贝，源 trace 清除后 item 仍可用 |
| `retrieved_contexts` | `item.context` | `context` 列语义就是 RAG passages，保持纯粹 |
| `conversation_history` | `metadata_.backflow.conversation_history` | 受 `max_turns` 约束；不进 `context`（避免与 RAG 语义混淆） |
| `tool_calls` / `agent_id` / `session_id` | `metadata_.backflow.*` | 溯源 + 未来 per-agent 切分语料的抓手 |
| `expected_answer` | 不写（留空） | 机分是溯源不是 ground truth；标注路径的人类标签才有资格写答案语义 |

`tags = ["trace-backflow", rule.name]`（对齐 7.4 的 `["human-annotation", queue.name]` 惯例；`metadata_.backflow` 另含 `task_id` / `rule_id` / `scores` 快照）。开窗直接使用 trace 的 `start_time`/`end_time`（与在线评分同窗，"低分样本"与"物化内容"严格对应）；消息数超过投影上限的窗口沿用 `build_eval_input` 的 skip 语义（不截断半截对话）。

### D6: 不做物化内容 DLP 扫描

`dlp-egress-filter` 的定位是出口过滤；物化内容留在平台内部 dataset，且人工路径（7.4）已确立"标注员看过 → 物化"的先例。若未来 dataset 导出 / 对外共享成为常场景，在那条链路统一接 DLP。记为显式决策而非疏漏。

### D7: API 形态

- `POST /api/evaluation/backflow-rules` / `GET .../backflow-rules` / `GET|PATCH|DELETE .../backflow-rules/{id}`（workspace 隔离，404 语义对齐既有 evaluation 路由）
- `POST /api/evaluation/backflow-rules/{id}/run` → `{created, skipped, dataset_id}`（对齐 `push_to_dataset` 返回形态，调用方心智一致）
- 规则读取 schema 附带 `last_run` 摘要（时间 + created/skipped 计数，存 `metadata_`），让"这个规则上一次跑进去多少条"可观测；不建独立 run 历史表（v1.5 若需要再拆）

## Risks / Trade-offs

- [JSON 列双键去重扫描随 dataset 增长变慢] → 单次 run 的去重集合内存内维护；`metadata_` 扫描仅限目标 dataset 的 items（与 7.4 现状同量级）；dataset 规模成为问题时再引入生成列/索引
- [规则配错（如分数带过宽）一次物化数千条] → `limit` 硬上限 + 空 filter 列表拒绝 + run 返回 created/skipped 让失控立即可见；后续可加"首次 run 超 N 条需确认"（v1.5）
- [同一 trace 分数后续被 override/重评，物化 item 不回写] → 记录的是物化时刻的分数快照，语义即"入库时它是因为这个分进来的"；7.4a 的 reconciliation 在报表层处理人评 override，不回流改动 dataset item
- [多轮历史放大 item 体积] → `max_turns` 上界 + 投影消息数上限沿用既有 skip 语义，不做截断

## Migration Plan

单张新表 `evaluation_backflow_rules` 的 alembic migration；无既有表变更、无数据回填。回滚 = downgrade 删表（物化出的 dataset items 保留——它们是普通 item，带 `trace-backflow` tag，可按 tag 清理）。

## Open Questions

（无——D1–D7 均已在设计期决策；"首 run 确认门"与 run 历史表为 v1.5 候选，不影响本 change 规格。）
