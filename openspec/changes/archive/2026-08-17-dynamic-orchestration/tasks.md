## 1. 公共契约与基础设施

- [x] 1.1 新建 `src/hecate/engine/dynamic_types.py`,定义 `TaskNode` / `TaskDAG` / `OrchestrationBudgets` / `VerifyConfig` Pydantic 模型,带 cycle / dangling reference / empty goal 校验
- [x] 1.2 在 `src/hecate/engine/types.py` 的 `NodeType` StrEnum 加 `COORDINATOR = "coordinator"` 值,确认 lowercase string
- [x] 1.3 在 `src/hecate/engine/eventstore.py` 的 `EventType` StrEnum 加 `ORCHESTRATOR_DECISION` 和 `ORCHESTRATOR_EVALUATION`,确认 LogPolicy 不豁免
- [x] 1.4 新建 `src/hecate/engine/orchestrator_validator.py`,导出 `validate_task_requirements(dag, roster)` 与 `ValidationReport`,实现 cycle / 指派 / 能力 / 引用悬挂四档 fail-closed

## 2. 枚举与模式识别

- [x] 2.1 在 `src/hecate/engine/patterns.py` 的 `CollaborationPattern` 加 `DYNAMIC = "dynamic"` 值
- [x] 2.2 在 `infer_pattern()` 加 COORDINATOR 节点识别分支(优先级最高,匹配即返回 DYNAMIC);保留其他 6 个静态模式的语义不变

## 3. Worker 实现

- [x] 3.1 新建 `src/hecate/engine/workers/coordinator_worker.py`,导出 `CoordinatorWorker`,实现 plan → validate → emit ORCHESTRATOR_DECISION → executor → emit ORCHESTRATOR_EVALUATION 主循环
- [x] 3.2 在 `src/hecate/engine/workers/__init__.py` 导出 `CoordinatorWorker`
- [x] 3.3 `CoordinatorWorker` 注入 `EnginePort.llm_invoke` 接口,实现 `planner_model` / `evaluator_model` / per-task model 三段路由
- [x] 3.4 `CoordinatorWorker` 持 `stall_counter` 与 `_ledger` channel 摘要读取,实现 Magentic 双循环图化(每轮 prompt 全新构建、stall 累积、stall_cap_exceeded 终止)

## 4. Executor 模板与物化

- [x] 4.1 在 `src/hecate/engine/templates.py` 加 `build_dynamic_orchestration_executor(dag, roster) -> GraphConfig`,产出子图根 + 同步 FAN_OUT + MERGE + 综合节点;节点 id 唯一化(`task_<task_id>_<revision>`)、channel id 唯一化(`<task_id>.<expected_output>`)
- [x] 4.2 实现 `max_concurrent` 调度约束:同依赖层级任务数 > `max_concurrent` 时,FAN_OUT 拆批
- [x] 4.3 实现 `synthesis_transform` 评估:复用 `VARIABLE_SET` 节点的表达式 evaluator,产出写 `_synthesis_buffer` channel
- [x] 4.4 实现 `verify` hook:非空时主 worker 完成后调 verifier agent,verifier 输出写 ledger 的 `verified` 字段
- [x] 4.5 实现 `carryover_outputs`:executor 显式声明(默认空),replan 跨 iteration 引用已完成 task 输出

## 5. 五重隔离契约

- [x] 5.1 子图执行走独立 `child_session_id`,新建 `ChannelManager`(复用 `engine/subgraph.py` 的 `execute_subgraph` 模式,但强化隔离)
- [x] 5.2 子图的 `ChannelManager` 注册时只声明 `channel_mapping.input` 列出的父通道;未声明通道 `read()` 抛 `KeyError`(复用既有 `ChannelManager` 语义)
- [x] 5.3 executor 回写时只写 `channel_mapping.output` 声明的通道;子图 `messages` 通道**不**回写父图
- [x] 5.4 子任务 agent 的长期记忆写入以子 session id 为键,跳过父 thread 的记忆 flush hook(DeerFlow `skip_memory_flush=True` 同源)
- [x] 5.5 失败判定只看 `WorkerResult.error` 与状态契约;不解析子 agent 输出文本猜成败(单元测试覆盖 "I cannot complete this task" 文本 vs `error = None` 的场景)

## 6. EventStore 集成与三轴预算

- [x] 6.1 `CoordinatorWorker` 在 `PregelRuntime._emit` 路径上发 `ORCHESTRATOR_DECISION` / `ORCHESTRATOR_EVALUATION`,payload 形态符合 spec §EventTypes 要求
- [x] 6.2 orchestrator 子图跑子前 emit `SUBGRAPH_START {child_session_id, task_count, model_planner}`,完成时 emit `SUBGRAPH_END {child_session_id, status}`(status = ok | failed | interrupted)
- [x] 6.3 三轴预算执行点:在每个 task dispatch 边界检查 `token_budget` + `max_total_tasks`;每个 iteration 边界检查 `max_iterations`;同依赖层级 dispatch 前检查 `max_concurrent`
- [x] 6.4 任何预算触发时 `ORCHESTRATOR_EVALUATION` payload 带 `stop_reason` 枚举 + `guidance_string = "reuse it, retry tighter, or raise budget"`;**不**新加 status 枚举

## 7. Prompt 与快照测试

- [x] 7.1 在 `src/hecate/engine/dynamic_types.py` 导出 `BENEFIT_BASED_DELEGATION_RUBRIC` 公开常量(DeerFlow `lead_agent/prompt.py` 同源 rubric 文本)
- [x] 7.2 `CoordinatorWorker` 的 planner system prompt 内嵌该常量;evaluator 与 worker 的 prompt 各自独立
- [x] 7.3 新建 `tests/test_coordinator_prompt.py`,snapshot 测试钉死 rubric 文本字节级一致;第二个测试若 rubric 改了却未更新 snapshot 必须 fail

## 8. 修改的 spec 测试

- [x] 8.1 更新 `tests/test_engine/test_collaboration_patterns.py`,加 `CollaborationPattern.DYNAMIC` 的 enum count + value 测试
- [x] 8.2 加 `infer_pattern()` 对 COORDINATOR 节点的识别测试

## 9. 端到端集成测试

- [x] 9.1 新建 `tests/test_engine/test_dynamic_orchestration.py`,覆盖 happy path:简单 goal + 2 worker roster → TaskDAG → execute → evaluate satisfied → return
- [x] 9.2 覆盖 stall path:planner 持续输出同质 DAG → stall_counter 累积 → stall_cap_exceeded → 返回 best effort
- [x] 9.3 覆盖 carryover:iter 2 TaskDAG 引用 iter 1 的 task 输出 → executor 从 `_ledger` 读取,iter 1 task 不重跑
- [x] 9.4 覆盖三轴预算:构造超 `max_total_tasks` 的 TaskDAG,验证 `stop_reason = "turn_capped"` 与 guidance_string
- [x] 9.5 覆盖 fail-closed:`validate_task_requirements` 拒绝 cycle / 缺失 agent / 能力不满足场景,coordinator emit `dag=None, reasoning=verification_failed`
- [x] 9.6 覆盖隔离:子图尝试读未声明父通道抛 `KeyError`;子图 `messages` 不回写父图
- [x] 9.7 覆盖事件持久化:`SUBGRAPH_START/END` + `ORCHESTRATOR_DECISION` + `ORCHESTRATOR_EVALUATION` 都进 EventStore;fold-to-version 可重建 plan 修订史

## 10. ADR 与文档

- [x] 10.1 新建 `docs/design/adr/032-dynamic-orchestration.md`,记录 data-as-plan 范式选择、C 路线定位、五重隔离契约、与 1.3.19 接缝;引用 ADR-001 / ADR-007 / ADR-030
- [x] 10.2 更新 `docs/design/engine-design.md`,在 "Self-Planning (Planned)" 一节替换为 "Dynamic Orchestration (1.3.18)",链接到 ADR-032
- [x] 10.3 验证 `docs/features/feature-catalog.md` 与 `docs/features/roadmap.md` 已在 2026-08-17 scope-freeze 中正确登记推迟项(1.3.18a + UI companion)

## 11. 完工前验证

- [x] 11.1 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/` 全绿
- [x] 11.2 `python -m pytest tests/ -q` 全绿(1713+ tests,本次新增 ~15 个测试)
- [x] 11.3 `openspec validate --change dynamic-orchestration --strict` 通过
- [x] 11.4 完整本地验证(`ruff + ruff-format + mypy + pytest`)无新增 warning/error 后,提示用户走 `/opsx:apply` 进入实施阶段