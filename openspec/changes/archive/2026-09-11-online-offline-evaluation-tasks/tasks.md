## 1. 数据模型与迁移

- [x] 1.1 `src/hecate/models/evaluation.py`：新增 `EvaluationTaskModel`（`task_type`、`name`、`status`、`evaluator_configs`、`config` JSON、`last_scanned_at`、`metrics` JSON、`workspace_id`，软删基类字段）与 `EvaluationTaskScoreModel`（`task_id`、`target_type`、`target_id`、`session_id`、`agent_id`、`metric_name`、`value`、`reasoning`、`source`、`status`、`workspace_id`）；`EvaluationRunModel` 增加可空 `task_id` 与 `summary` JSON 列
- [x] 1.2 同文件新增 Pydantic schema：`EvaluationTaskCreateSchema/UpdateSchema/ReadSchema`（offline/online 配置校验：`sampling_rate ∈ (0,1]`、evaluator 非空等）、`EvaluationTaskScoreReadSchema`；`EvaluationRunReadSchema` 增量 `task_id`/`summary`
- [x] 1.3 Alembic migration：建两表（score 表含唯一索引 `(task_id, target_type, target_id, metric_name)` 与查询索引 `task_id`、`session_id`）、`evaluation_runs` 加列；核验 `traces` 扫描路径索引，不足则补 `(type, status, created_at)` 组合索引
- [x] 1.4 `tests/test_evaluation/`（或既有评估测试目录）：模型与迁移的单测（schema round-trip、唯一约束生效）

## 2. 任务服务层与 API

- [x] 2.1 `src/hecate/ops/evaluation/tasks/service.py`：`EvaluationTaskService` — CRUD + workspace 隔离 + 创建时校验（evaluator 名单可经 `get_evaluator_class` 解析；online 任务必填 `agent_id`/`sampling_rate`；offline 任务必填 `dataset_id`；`answer_source=agent` 需任务配置 `agent_id`）
- [x] 2.2 enable/disable 语义：`enable` 校验 evaluator 注册状态后置 `active`；`disable` 置 `disabled`（offline 任务仅允许经 update 停用）；拒绝对 `disabled` 任务触发 run/enable
- [x] 2.3 `src/hecate/ops/api/evaluation_tasks.py` 新 router：`POST/GET/GET{id}/PUT/DELETE /api/evaluation/tasks`、`POST {id}/enable`、`POST {id}/disable`、`POST {id}/runs`（202）、`GET {id}/runs`、`GET /api/evaluation/scores`（分页 + `task_id/target_id/session_id/metric_name/status` 过滤）；注册进 ops API 聚合
- [x] 2.4 API 测试：CRUD、校验失败分支、workspace 隔离、enable/disable 生命周期、scores 查询过滤与分页

## 3. 离线任务执行

- [x] 3.1 `src/hecate/ops/evaluation/tasks/runner.py`：任务 run 执行器 — 建 `evaluation_runs`（`task_id`、`queued`）→ `asyncio.create_task` + 独立 session 执行 → `running → completed/failed`，复用 7.2b job 模式（参照 `synthesis/job.py`）
- [x] 3.2 `engine.py` 扩展：`run()` 支持 `answer_source=agent` — per-item 无预置答案时经 `RuntimePort.agent_execute` 逐条调用被测 agent（任务 config 取 `agent_id`），单条失败记 `-1.0` error score 不中断；既有 manual/pipeline 行为不变
- [x] 3.3 summary 计算：`threshold` 配置时产出 `{total_items, passed_items, failed_items, pass_rate, metric_averages}`；`baseline_run_id` 配置时复用既有 compare/regression 对比逻辑追加 `regressions`；写入 `evaluation_runs.summary`
- [x] 3.4 `POST {id}/runs` 接线 runner + run 列表查询（`GET {id}/runs` 与 `GET /api/evaluation/runs?task_id=`）
- [x] 3.5 测试：异步生命周期状态迁移（mock runner）、agent 答案路径（stub RuntimePort：成功/失败/隔离）、threshold/baseline summary、run 列表

## 4. 在线评分 worker

- [x] 4.1 `src/hecate/ops/evaluation/tasks/trace_input.py`：session 事件流 → `EvalInput` builder — `EventStore.replay(session_id)` 按 trace `start_time/end_time` 开窗，窗内最后一条 user 消息为 query、最后一条 assistant 消息为 generated_answer、其余为 `conversation_history`，`TOOL_CALL` 事件聚合 `tool_calls`；窗内无完整 user+assistant 消息对时跳过该样本并记日志；重放消息条数设上限
- [x] 4.2 `src/hecate/ops/evaluation/tasks/online_worker.py`：常驻 asyncio 循环 — 遍历 `active` online 任务 → 水位线查询（`traces JOIN sessions` 过滤 `sessions.agent_id`、`type='trace' AND status='completed' AND traces.created_at > last_scanned_at`，`max_traces_per_cycle` 截断）→ 确定性采样（hash(trace_id)）→ builder + evaluator 评分 → `ON CONFLICT DO NOTHING` 写 score → 推进水位线 + 任务 `metrics` 累计（scanned/sampled/scored/error）
- [x] 4.3 评分失败隔离：单 evaluator 异常 → error score（`status='error'`，reasoning 记因）；单 trace 异常 → 跳过记日志，循环继续
- [x] 4.4 组合根接线（`core/composition/wiring.py`）：feature flag `evaluation.online_scoring.enabled`（默认关）控制启动；pg advisory lock 防多进程重复消费；进程退出钩子优雅停止
- [x] 4.5 测试：采样确定性（同 trace_id 同决策、rate=1.0 全采）、水位线推进与重启续扫、幂等唯一键去重、volume cap、失败隔离、builder 容错（advisory lock 以单测验证获取/释放逻辑即可）

## 5. 收尾验证

- [x] 5.1 全量校验：`ruff check`、`ruff format --check`、`mypy src/`、`python -m pytest tests/ -q` 全绿
- [x] 5.2 手工链路验证（本地 docker 基础设施）：创建 offline 任务（agent 答案源）触发 run → 202 → 轮询 completed + summary；开 flag 创建 online 任务 → 造 trace → scores 查询可见；disable 后不再产出
- [x] 5.3 `openspec validate --strict` 通过；核对 spec 场景逐条有对应测试映射
