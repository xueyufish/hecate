# Proposal: human-annotation-trace-backflow (7.4 + 7.4a)

## Why

自动化评估回路已经闭环（7.1/7.2/7.2a/7.2b/7.2c/7.2e/7.3 均已交付），但生产流量"只出不出"：`traces` 被在线任务用 LLM judge 打分后，没有任何人工环节能纠正机器的判断——judge 会错判（AgentScope PawBench 实证：仅 harness 差异即可造成 10+ 分漂移），而错判至今无法被标记、覆盖或回流。业界已收敛出标准形态（Langfuse/LangSmith 的 annotation queue、watsonx.governance 的 override + justification、AWS Bedrock 的 humanEvaluationConfig、OpenJudge 的人工标注→rubric 迭代）；国产平台（华为 AgentArts、美团 CatPaw）公开资料均未提供人评校准，是明确的产品差异化点。Schema 层早已预留：`evaluation_scores` / `evaluation_task_scores` 的 `source` 列 docstring 列明 `"human"`，7.2e 报表已按 `source=human` 分色渲染等人评数据落库。本变更交付 feature-catalog `7.4`（Human Annotation，M）+ `7.4a`（Human Score Calibration，S），其依赖项已全部就绪。

## What Changes

- **新增标注队列两个实体**（`annotation_queues` / `annotation_queue_items`）：队列含名称、说明（instructions）、指标定义（`metric_defs`：numeric 区间 / categorical 枚举 / boolean，约束人评取值域）、指派成员（`assigned_user_ids`）；item 引用一个 root trace（`target_type=trace`），状态机 `pending → claimed → completed | skipped`，记录领取人与完成人。workspace 隔离的一等 CRUD 资源。
- **标注工作台（数据展示复用既有投影）**：item 详情用 `trace_input.py` 同源投影展示该 trace 窗口内的消息与工具调用，并**预填**在线任务已产出的 llm_judge 分数（suggestion 模式）——人做的是"校准机器分"，不是从零打分。
- **人评分写入同一本分数账本**：提交标注落 `evaluation_task_scores` 新行（`source="human"`、`task_id=NULL`、`annotator_id` 记录提交人），与机分 **coexist**（互不覆盖），供校准分析做配对样本；即使人分与机分同值也建行（"确认机分"本身是校准数据）。
- **7.4a 校准语义 = 显式 override，而非 in-place 改写**：override 是一条带 `overrides_score_id`（指向被覆盖的机分行）的 human 行，`reason_code`（结构化原因码，override 时必填）+ `justification`（自由文本）必填；原始机分行永不删除。聚合 reconciliation 规则：同一 `(target_id, metric_name)` 存在 override 时取 human 值，否则取自动化值。
- **入队通道 v1**：手动添加（单条 / 批量 ≤100）；一步式"从 online task 建队列"——按 agent / metric / 分数阈值 / 采样上限筛选在线机分样本批量入队。
- **人验证回流（trace backflow 的半边）**：completed item 一键"推入 dataset"——以投影消息构造 item（`query`/`generated_answer`），人评标签写入 `metadata_` 与 `tags`（含来源队列与 trace id 溯源），幂等去重（同 dataset 同 trace 不重复物化）。7.2d（自动化回流）据此缩水为"无人参与的批量物化"，范围在 roadmap 中同步收窄。
- **校准分析 API + 报表校准视图**：按 metric 输出人机配对样本数、agreement rate、MAE（numeric）、Cohen's Kappa（categorical/boolean）、分桶热图数据——即 judge 质量的持续监控面板（openworker live/shadow 思想的落地形态）。
- **新 API**（约 10 端点，router `evaluation_annotations.py`）：队列 CRUD、item 列表/详情/入队（单条+批量+从 task 筛选）、claim/skip/submit、push-to-dataset、`GET /api/evaluation/calibration` 校准分析。
- **前端**：`/ops-center/evaluation` 新增第五视图 Annotation（队列列表 + 工作台）与第六视图 Calibration（校准指标卡 + 热图）；Online 视图 session 下钻与在线低分样本处提供"加入标注队列"入口。
- **不改动**：既有同步 runs、offline run/compare API、在线 worker 采样与打分逻辑全部保持兼容；`evaluation_scores`（离线 run 分数）不参与人评。

**明确不在本变更范围**（Non-goals，防止与兄弟特性互相渗透）：session / span 粒度标注（`target_type` 预留位）；离线 run 结果（`evaluation_scores`）的人工标注与 override；pairwise 偏好队列（RLHF/DPO 原料，v2）；多人评审与标注者间一致性（LangSmith `num_reviewers` 模式，v1.5）；自动化规则持续入队（v1.5）；override 的 sign-off / 审批链（企业域审批设施承接）；人工标注 → rubric 自动生成（7.2f）；7.2d 自动化 trace 回流（独立 change）；历史 trace 标注 backfill。

## Capabilities

### New Capabilities

- `human-annotation`: 标注队列生命周期（CRUD / 指派 / 领取 / 完成与跳过）、入队通道（手动 + 从在线任务筛选）、标注提交与人评落账本（coexist + suggestion 预填）、override 校准语义（overrides_score_id + reason_code + justification，原行保留）、人验证回流（标注 → dataset 幂等物化）、校准分析（agreement / MAE / Kappa / 热图）。

### Modified Capabilities

- `evaluation-tasks`: "Target-typed score storage and query" requirement 增量——`task_id` 放宽为可空（`NULL` 表示人工标注行），新增 `annotator_id` 等人工字段，scores 查询支持 `source` 过滤并返回人工字段；既有在线/离线语义不变。
- `evaluation-report-dashboard`: overview / trends / distributions 聚合引入 reconciliation 规则（override 存在时 human 值胜出）；新增 Calibration 视图 requirement（校准指标卡与热图渲染）与评估页视图数量从四到六的增量。

## Impact

- **数据模型/迁移**：`src/hecate/models/evaluation.py` 新增 `AnnotationQueueModel`、`AnnotationQueueItemModel`；`EvaluationTaskScoreModel` 加可空列 `annotator_id`、`overrides_score_id`、`reason_code`，`task_id` 转可空，唯一键改 partial unique index（`WHERE task_id IS NOT NULL`）；另加查询辅助索引（支撑人评 upsert 幂等与校准配对）；一个 alembic migration（无存量数据，一次到位）。
- **评估域代码**：`src/hecate/ops/evaluation/annotation/`（queue service、item workflow、dataset push、calibration service）；复用 `tasks/trace_input.py` 投影与 `dataset_service.py` 物化路径。
- **API**：新增 `src/hecate/ops/api/evaluation_annotations.py` router（`get_auth_context` 鉴权，annotator 取 `AuthContext` 用户身份）；`main.py` 注册。
- **报表**：`ops/evaluation/reports/service.py` 聚合口径接入 reconciliation；新增 calibration 聚合。
- **前端**：`web/src/components/evaluation/` 新增 `annotations-view.tsx`、`calibration-view.tsx` 及 API client 增量；评估页 tab 扩到六个。
- **组合根**：无新增后台 worker / feature flag（纯 CRUD + 按需聚合，无运行时行为需要灰度）。
- **依赖**：仅复用既有依赖（SQLAlchemy、FastAPI、Recharts），无新三方包。
- **下游铺路**：7.2d 自动化回流缩水立项；7.2f（judge rubric 反哺）以 reason_code 语料与校准面板为数据源；8.12（质量门禁）以人验证标签为 golden set 来源。
