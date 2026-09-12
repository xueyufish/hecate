# Design: human-annotation-trace-backflow (7.4 + 7.4a)

## Context

自动化评估底座已就位：`traces` 表（观测中心单表设计）由 OTel span processor 落库；在线任务经 `tasks/online_worker.py` 采样后由 `tasks/trace_input.py` 从 session 事件流投影 `EvalInput`；评分统一落 `evaluation_task_scores`（target 类型化、`(task_id, target_type, target_id, metric_name)` 幂等）；7.2e 报表在该表上做按需聚合并渲染 `source` 三色徽标（含预留的 `human`）。鉴权走 `core.deps_workspace.get_auth_context`（`AuthContext` 携带 workspace 与用户身份）。业界形态见 proposal 引用的调研（Langfuse/LangSmith 队列、watsonx override、Bedrock humanEvaluationConfig、OpenJudge rubric 迭代）；本设计的取舍以此为基线。

## Goals / Non-Goals

**Goals:**

- 人评与机分共用一本 target 类型化分数账本，coexist 可配对，override 可审计
- 队列工作流最小完备：建队、入队（手动 + 从在线任务筛选）、领取、提交/跳过
- 人验证回流：完成标注一键物化进 dataset（幂等），给 7.2d 缩水后的自动化回流让出边界
- 校准分析（agreement / MAE / Kappa / 热图）+ 报表聚合 reconciliation 口径一次定清

**Non-Goals:**

- session/span 粒度标注、pairwise 偏好队列、多人评审与标注者间一致性（v1.5/v2）
- 离线 `evaluation_scores` 的标注与 override（run 不可变，错了重跑）
- sign-off / 审批链（企业域审批设施承接）、自动化规则持续入队（v1.5）
- 人工标注 → rubric 自动生成（7.2f）；7.2d 自动化回流；历史 backfill
- 后台 worker / feature flag（无运行时行为需要灰度）

## Decisions

### D1. 人评分落同一本账：`evaluation_task_scores`，不建独立标注分数表

业界共识（Langfuse Score 的 `source` 枚举、LangSmith 单一 Feedback 对象、Phoenix `annotator_kind`）都是单表 + source 区分。7.4a 的数学前提是人分与机分是**同一度量空间的配对观测**：同表让校准分析成为一次 self-join（`target_id + metric_name` 配对），7.2e 的 source 徽标与 scores 查询零改造可用。代价是 `task_id` 需转可空。备选"独立 `human_annotations` 表"被否：所有报表聚合与校准都要跨表 UNION/JOIN，且 `GET /api/evaluation/scores` 语义分裂。

### D2. override = 指针型 supersede，原行永不改删

人评 override 行携带 `overrides_score_id` 指向被覆盖的机分行；原始机分保留是两个功能的共同前提——审计链（watsonx 的 Override 对象语义）与 agreement 配对统计（agreement 需要机分原值）。in-place UPDATE（改写原行 value）被否：丢原始观测，无法回算一致性。reconciliation 完全在读侧实现（报表聚合时以"最新 override 胜出、否则机分、普通人评行不参与聚合"取值），写侧不做任何联动改写——语义集中在报表 service 一处，避免多写入口。

### D3. 队列模型最小化：两表 + JSON 指派，不建 assignment 表

`annotation_queues`（含 `metric_defs` JSON、`assigned_user_ids` JSON）+ `annotation_queue_items`（`queue_id`、`target_id`、`target_type`、状态机 `pending → claimed → completed | skipped`、`added_by`/`claimed_by`/`claimed_at`/`completed_by`/`completed_at`）。唯一约束 `(queue_id, target_id, target_type)` 实现"一 trace 一队列至多一条"。指派用 JSON 列而非关联表（Langfuse `assignedUsers` 形态）：v1 协作规模下查询与校验都简单，`assigned_user_ids` 非空时仅指派者可领取。多评审（LangSmith `num_reviewers` + reservation）留 v1.5，届时再抽表不迟。

### D4. 迁移：`task_id` 转可空 + partial unique index

自动评分行的唯一键 `(task_id, target_type, target_id, metric_name)` 改为 partial unique index（`WHERE task_id IS NOT NULL`）——PG 默认 `NULLS DISTINCT` 语义下 NULL 不触发唯一冲突，partial index 是"仅自动行唯一"的标准建模；当前无生产数据，迁移一步到位、无兼容包袱。PG 用 `Index(..., postgresql_where=...)`；SQLite（测试内库）原生支持 `CREATE UNIQUE INDEX ... WHERE`，SQLAlchemy 方言可直出，测试兼容。人评行幂等采用 upsert 语义（同 annotator 同 target 同 metric 更新自己的行），建辅助普通索引而非唯一约束（override 行与普通行并存，唯一键含 `overrides_score_id` 会使 upsert 判断复杂化，不值得）。

### D5. 取值域约束内嵌队列：`metric_defs` JSON，不建 ScoreConfig 表

Langfuse 用独立 ScoreConfig 对象支持跨队列复用与归档；Hecate v1 每队列自带 `metric_defs`（`name` + `data_type` + numeric 区间 / categorical 枚举），提交时校验 value 落域。校准队列复用 evaluator metric 名是**约定**（name 对齐已注册 evaluator 的 metric 即自然配对），不做 registry 硬校验——人评 metric 名与机分不同名时只是配对数为 0，语义无害。跨队列共享定义等到真实复用需求出现（v1.5）再抽表。

### D6. suggestion 预填 = 读时实时查询，不落 suggestion 存储

Argilla 把 LLM 建议持久化为 `suggestions` 集合；本设计不持久化——item 详情实时查该 trace 的最新机分（已存在的 `evaluation_task_scores` 行）作为只读 `suggestion` 返回。理由：机分行本来就在账本里，存一份建议等于双写；且预填的值应随新机分（如重扫）自然更新。前端把 suggestion 作为表单默认值，人"接受或修改"。

### D7. `reason_code`：自由字符串 + 建议词表，override 必填

结构化 reason 是 7.2f（rubric 反哺）的语料基础（openworker reviewer 词表 + Anthropic rubric 校准纪律）。v1 不建 enum 表：`reason_code` 为 ≤50 字符自由串，API 文档给出建议词表（`judge_wrong_fact` / `judge_too_harsh` / `judge_too_lenient` / `missing_context` / `labeling_error` / `other`），词表演化靠文档 + 后续统计。自由文本 `justification` 单独一列（落 `reasoning` 同形字段），两者分开是因为"可统计的原因"与"可读的解释"用途不同。

### D8. dataset 回流映射与幂等：metadata 溯源 + 物化前查重

item → dataset item 映射：`query` = 投影窗口内最后一条用户消息、`generated_answer` = 最后一条助手消息、`metadata_.annotation = {trace_id, queue_id, queue_item_id, labels}`、`tags` 追加 `human-annotation` 与队列名；`expected_answer` 留空（ground truth 在 labels 里，比对类 evaluator 需要时由人补录）。幂等键 = `metadata_.annotation.trace_id` 在目标 dataset 内的存在性检查（物化前查询，无唯一约束）。并发推送竞态在 v1 接受（单 workspace 低频人工操作），代码留 TODO，后续如需可加 dataset 级 advisory lock。`dataset_name` 首推自动建 dataset，复用 `dataset_service` 既有路径。

### D9. 权限与身份：workspace 成员即可标注，身份取自 `AuthContext`

v1 不引入 per-feature 角色：workspace 成员可建队/入队/标注（`assigned_user_ids` 非空时仅指派者可领取）；不接 sign-off 审批链（watsonx 的治理级语义，由企业域审批设施将来承接）。`annotator_id` 取 `AuthContext.user_id`（`uuid.UUID`，见 `core/auth_context.py`）。所有 mutation 落人（`added_by`/`claimed_by`/`completed_by`/`annotator_id`），满足"全量审计"的最简形态。

### D10. 前端：评估页扩到六视图，校准独立成视图

Annotation 视图（队列列表 + 工作台）与 Calibration 视图（指标卡 + 热图，Recharts）并列新增；工作台表单控件按 `data_type` 渲染（numeric 数字输入、categorical 下拉、boolean 开关），override 开关展开 reason_code 输入 + justification 文本域。入口动作（"加入标注队列"）挂在 Online Quality 的 session 下钻与低分样本列表。报表聚合的 reconciliation 在 tooltip 注明口径（"含人工 override"），breakdowns 保留 raw 视图作对照。校准指标小样本口径：直接展示 agreement/Kappa 数值并显式标注 `pair_count`，v1 不做置信区间或置信提示。

## Risks / Trade-offs

- [partial index 的方言差异] PG / SQLite 语法略有差异 → SQLAlchemy `postgresql_where` 在 SQLite 降级为通用 WHERE 子句，测试覆盖建表 + 插入冲突两条路径
- [普通人评行被误计入聚合] coexist 语义下未 override 的人评行若进聚合会双重计数 → reconciliation 规则在 spec 固化（"非 override 人评行不参与聚合"），报表 service 单点实现
- [跨队列标注同一 trace 产生多行人评行] upsert 键是 `(annotator_id, target_id, metric_name)`，跨队列重标更新不新增；不同 annotator 的行并存是有意设计（多人评审的前置数据）
- [推送竞态重复物化] 幂等靠物化前查重，无唯一约束兜底 → v1 接受（低频人工操作），留 TODO
- [队列无界增长] v1 入队只有手动（批量 ≤100）与 from-task（limit 默认 50）→ 有硬上限；持续规则入队留 v1.5 时一并做配额
- [metric 名对齐无硬校验] 校准队列 metric 名与 evaluator 不一致时配对为 0 → 队列表单提示可用的 evaluator metric 名（读 registry），不做强校验

## Migration Plan

1. Alembic migration（单条，无存量数据，一次到位）：建 `annotation_queues` / `annotation_queue_items` 两表；`evaluation_task_scores` 加 `annotator_id`、`overrides_score_id`、`reason_code` 可空列；`task_id` 转 nullable；旧唯一约束替换为 partial unique index（`WHERE task_id IS NOT NULL`）+ 人评辅助索引。
2. 前后端同 PR 发布；报表 reconciliation 与新视图同版本生效，无双口径期。既有 API 行为不变（`GET /api/evaluation/scores` 仅增可选 filter 与响应字段）。
3. Rollback：downgrade 直接删两表、三列与新索引并恢复 NOT NULL——无生产数据，无数据保全逻辑。

