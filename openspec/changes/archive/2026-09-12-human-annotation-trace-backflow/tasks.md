# Tasks: human-annotation-trace-backflow (7.4 + 7.4a)

## 1. 数据模型与迁移

- [x] 1.1 `src/hecate/models/evaluation.py`：新增 `AnnotationQueueModel`（`name`/`description`/`instructions`/`metric_defs` JSON/`assigned_user_ids` JSON，唯一索引 `(queue_id, target_id)` 放 item 表）与 `AnnotationQueueItemModel`（`queue_id`/`target_type`/`target_id`/`status` ∈ pending|claimed|completed|skipped/`added_by`/`claimed_by`/`claimed_at`/`completed_by`/`completed_at`，唯一约束 `(queue_id, target_id, target_type)`）+ 对应 Pydantic Create/Read schema
- [x] 1.2 `EvaluationTaskScoreModel` 增量：加可空 `annotator_id`、`overrides_score_id`、`reason_code`；`task_id` 转可空；schemas 补字段
- [x] 1.3 Alembic migration（单条，无存量数据）：建两新表；`evaluation_task_scores` 加列 + `task_id` nullable + 旧唯一约束替换为 partial unique index（`WHERE task_id IS NOT NULL`）+ 人评辅助索引 `(target_id, metric_name, source)`；downgrade 直接删两表/三列/新索引并恢复 NOT NULL
- [x] 1.4 迁移测试：SQLite 下 partial index 生效（同 task 重复插分被拒、`task_id=NULL` 多行可并存）

## 2. 标注服务层

- [x] 2.1 创建 `src/hecate/ops/evaluation/annotation/__init__.py` 与 `service.py`：队列 CRUD（`metric_defs` 校验：name 唯一、data_type 合法、numeric 带 min/max 或 categorical 带非空 categories）、workspace 隔离
- [x] 2.2 入队：单条/批量（≤100）/`from-task`（按 `task_id` + `metric_name`/`min_score`/`max_score`/`limit` 筛 `evaluation_task_scores` 行取 target trace，跳过已在队列的 trace，limit 默认 50）；入队校验 trace 为本 workspace 的 completed root trace
- [x] 2.3 工作流：claim（`assigned_user_ids` 非空时仅指派者，记录 `claimed_by/at`）、skip、submit（按 `metric_defs` 校验 value 落域；写 `source="human"`、`task_id=NULL`、`annotator_id`、denormalize `session_id`/`agent_id`；item → completed）；completed/skipped 后 claim/submit 拒绝（409）
- [x] 2.4 override 校验：`overrides_score_id` 必须指向同 `target_id` 同 `metric_name` 的非 human 行，override 时 `reason_code`（≤50 字符）与 `justification` 必填；原行不改不删；同 annotator 同 `(target_id, metric_name)` 重提交走 upsert
- [x] 2.5 item 详情：复用 `tasks/trace_input.py` 投影窗口消息与工具调用；实时查该 trace 最新机分作只读 `suggestion`（含 source/value/reasoning）
- [x] 2.6 dataset 回流 `push-dataset`：`dataset_id` 或 `dataset_name`（不存在则建）；仅 completed 且带 ≥1 标注的 item；`query`/`generated_answer` 取投影首尾消息、`metadata_.annotation = {trace_id, queue_id, queue_item_id, labels}`、`tags` 追加 `human-annotation` + 队列名；物化前按 `metadata_.annotation.trace_id` 查重（幂等，留并发 TODO）；返回 created/skipped 计数
- [x] 2.7 校准服务 `calibration.py`：按 metric 配对（同 `target_id` + `metric_name` 的机分行 × 人评行）；numeric → `agreement_rate`（容差 0.1）+ `mae` + 10×10 分桶热图；categorical/boolean → exact-match `agreement_rate` + Cohen's Kappa；报告 unpaired 计数；`agent_id`/`task_id`/`metric_name` + 时间窗过滤；workspace 隔离

## 3. API 端点

- [x] 3.1 创建 `src/hecate/ops/api/evaluation_annotations.py`：`POST/GET /evaluation/annotation-queues`、`GET/PUT/DELETE /evaluation/annotation-queues/{id}`、`GET/POST .../items`、`POST .../items/from-task`、`GET .../items/{item_id}`、`POST .../items/{item_id}/claim|skip|submit`、`POST .../{id}/push-dataset`；`get_auth_context` 鉴权、分页、422/404/409 语义
- [x] 3.2 `GET /api/evaluation/calibration` 端点（filters：`agent_id`/`task_id`/`metric_name`/窗口）
- [x] 3.3 `GET /api/evaluation/scores` 增加 `source` 过滤参数，响应补 `annotator_id`/`overrides_score_id`/`reason_code`；`main.py` 注册新 router

## 4. 报表 reconciliation 与校准聚合

- [x] 4.1 `ops/evaluation/reports/service.py`：实现 reconciliation 取值（同 `(target_id, metric_name)` 最新 override 胜出、否则机分、非 override 人评行不进聚合），overview / trends / distributions / sessions 四处聚合接入；breakdowns 保持 raw
- [x] 4.2 overview 响应口径说明字段（tooltip 用文案，注明"含人工 override"）

## 5. 前端

- [x] 5.1 `lib/api-client.ts`：annotation-queues / items / calibration / push-dataset 类型与调用函数
- [x] 5.2 评估页 tab 扩到六个（Overview / Online / Runs / Compare / Annotation / Calibration）
- [x] 5.3 `annotations-view.tsx`：队列列表（名称、说明、各状态计数、metric chips、指派人）+ 建队/编辑表单（metric defs 动态行）+ 入队对话框（手输 trace 或 from-task 筛选）
- [x] 5.4 队列工作台：投影会话 + 工具调用展示、suggestion 预填（按 `data_type` 渲染控件：numeric 输入 / categorical 下拉 / boolean 开关）、override 开关（展开 reason_code + justification）、submit / skip、上一条/下一条导航
- [x] 5.5 `calibration-view.tsx`：per-metric 指标卡（pair_count / agreement_rate / mae 或 kappa）+ 机器×人工热图（Recharts）+ agent/metric 筛选 + 空态
- [x] 5.6 Online Quality session 下钻与低分样本列表加"加入标注队列"入口动作

## 6. 测试

- [x] 6.1 `tests/test_evaluation/test_annotation_service.py`：队列 CRUD 校验、批量入队与去重、from-task 筛选、claim 权限（指派/非指派）、submit 落域校验与 human 行落库（coexist + 原行不变）、override 校验（reason 缺失 422 / 跨 target 422）、upsert 语义、push-dataset 映射与幂等
- [x] 6.2 `tests/test_api/test_evaluation_annotations_api.py`：端点状态码语义（404 跨 workspace、409 状态机、422 校验）、calibration 数值场景（10 对样本 agreement 0.9 / mae、categorical kappa、workspace 隔离）
- [x] 6.3 `tests/test_evaluation/test_reports_reconciliation.py`：overview/trends/distributions/sessions 的 override 胜出与非 override 双计排除、breakdowns raw 保持
- [x] 6.4 前端 vitest：六 tab 导航、工作台提交流、calibration 卡渲染、空态

## 7. 验证与收尾

- [x] 7.1 `ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/` 全绿
- [x] 7.2 `python -m pytest tests/ -q` 相关层全绿
- [x] 7.3 `docs/gotchas.md` 补一条：人评行 `task_id=NULL` 与 partial unique index 语义（若实现中发现非显然行为）
- [ ] 7.4 archive 前更新 `docs/design/positioning.md` 与 feature-catalog：7.4/7.4a 交付描述、7.2d 缩水为自动化回流；评估页视图数 4→6
