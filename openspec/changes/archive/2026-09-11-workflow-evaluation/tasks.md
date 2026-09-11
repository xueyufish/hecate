## 1. Schema migration

- [x] 1.1 在 `src/hecate/models/evaluation.py` 的 `EvaluationRunModel` 增加可空列 `workflow_id`（UUID FK → `workflows.id`，index）、`workflow_version`（int nullable）、`dataset_snapshot`（JSONB nullable）、`repetitions`（int nullable）；同步 `EvaluationTaskModel.config` 字段解析路径支持 `workflow_id`/`workflow_version`/`repetitions`/`max_total_executions`/`max_in_flight`，所有列 nullable 向后兼容。
- [x] 1.2 生成 alembic 迁移（down_revision 接 7.2c 的 head），新增列全部 nullable；离线回滚不破坏既有 run 行。

## 2. 执行路径（AnswerSource=workflow）

- [x] 2.1 `src/hecate/ops/evaluation/types.py` 给 `AnswerSource` 枚举加 `WORKFLOW = "workflow"`，保持既有三个值不变。
- [x] 2.2 `src/hecate/ops/evaluation/engine.py` 新增 `_generate_answer_via_workflow`，按既有 `_generate_answer_via_agent` 同款模式（函数级懒导入 `WorkflowExecutionService`）；每条目新建一次性 session、非流式执行、捕获最终输出作 `generated_answer`、捕获每节点执行数据写入 run 载荷 `node_results`。
- [x] 2.3 同文件新增 `_summarize_repetitions`，repetitions > 1 时输出 `pass_rate`（任一次过）与 `consistency_rate`（每次都过），沿用既有 `summary` 落库路径。
- [x] 2.4 `src/hecate/ops/evaluation/tasks/service.py` 扩展 `build_task_config` 接受 workflow 字段；扩展 `validate_task` 校验 workflow_id 存在、workflow_version 可解析（任务启动时再次校验，registry 同款"启动期 vs 触发期"语义）；护栏 `max_total_executions`/`max_in_flight` 默认 1000/4，配置非正整数拒绝。
- [x] 2.5 `src/hecate/ops/evaluation/tasks/runner.py` 把 workflow 分支接进 `OfflineTaskRunner._execute` 既有生命周期（`pending → running → completed/failed` 不变）；单 run 启动时把数据集条目 JSON + content hash 落进 `dataset_snapshot`，完成时计算与 dataset 当前 hash 的漂移摘要写进 `summary.dataset_drift`。
- [ ] 2.6 新增 run session 标识：每次执行新建 session 时在 session metadata 写 `purpose="workflow_evaluation"`，让 `ops/retention/event_retention_service.py` 可按 purpose 过滤/缩短 TTL，避免评估 noise 影响租户会话视图。

## 3. 回归面（diff API + summary 升级）

- [x] 3.1 新增/落实现 `POST /api/evaluation/runs/compare`（承接 `regression-testing` spec 已声明接口）：响应增 `workflow_version`（两侧）、`token_usage_delta`/`latency_delta`/`cost_delta`、`dataset_drift`、`node_drift`（节点集匹配时）；与 7.2c 同款前置校验，dataset / 评估器集合不可比时返 422。
- [x] 3.2 新增 `POST /api/evaluation/workflow-evaluations/{workflow_version}/runs` 触发端点，复用 7.2c 的 202 + run_id 异步语义；运行前预检 `items × repetitions ≤ max_total_executions`，超阈返 400。
- [x] 3.3 `EvaluationEngine.run` 与 `OfflineTaskRunner._execute` 之间的并发控制：在 runner 内用 asyncio.Semaphore（容量 = `max_in_flight`）包裹 per-item 执行循环，保证峰值不超。

## 4. 出口面（publish 报告 + CLI）

- [x] 4.1 `src/hecate/studio/api/workflows.py` 的 `POST /workflows/{workflow_id}/publish/{version}` 响应增 `evaluation_report`（service 层组装，API 路径不动）：从最新 run 拉取 summary + 与已发布版本最近 run 的 diff；无 run 时 `evaluation_report: null`，publish 仍 200 OK。
- [x] 4.2 `src/hecate/cli/commands/workflow.py` 新增 `eval run` 子命令：参数 `--dataset`/`--workflow-id`/`--workflow-version`/`--repetitions`/`--baseline-run-id`，输出 JSON；退出码三态（0 通过、2 dataset 漂移、3 regression）；不渲染 PR comment（按 D7 解耦）。

## 5. 验证

- [ ] 5.1 ruff/format/mypy 全绿；新文件落 `tests/test_ops/test_evaluation/test_workflow_evaluation.py`（含 in-memory SQLite + StubRuntimePort + StubWorkflowExecutionService 假实现）。
- [ ] 5.2 单测覆盖：每个 spec scenario 至少一条对应 test；尤其 dataset_snapshot 漂移判定、repetitions 聚合、护栏触发、CLI 退出码三态、publish 报告 null 路径。
- [ ] 5.3 端到端 smoke：手动起一个最简 3 节点 workflow + 2 条数据集 + correctness evaluator，跑一次 run，断言 run 状态 completed、score 落库、diff endpoint 返回正确 metric delta、publish API 响应里出现 `evaluation_report`。
- [ ] 5.4 并发撞锁验证：100 并发触发同一 task 的 run 不撞 in-flight-turn 409（验证 `R4` mitigation）。
- [ ] 5.5 `tests/test_layering_domain.py` 与 `tests/test_runtime/test_runtime_self_sufficiency.py` 双绿（验证 ops → studio 懒导入未越界）。

## 6. 文档

- [ ] 6.1 新增 `docs/research/2026-09-workflow-evaluation-competitive-analysis.md`（proposal 引用的证据基础；含受限站点"待人工复核"清单）。
- [ ] 6.2 `openspec/changes/workflow-evaluation/archive/` 后更新 `docs/features/feature-catalog.md`（7.3 描述补完 deferred 项 7.3a/b/c + 7.2e 承接 UI）与 `docs/features/roadmap.md`（"→ P4"行按"✅（YYYY-MM-DD）"格式回填；该步骤在 archive 时执行，按 AGENTS.md `On /opsx-archive` 规则）。