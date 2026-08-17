## Context

Hecate 的图执行引擎(`PregelRuntime` + `ChannelManager` + `EventStore`)已经为动态编排留齐了 substrate(参见 ADR-001 graph-first、ADR-007 multi-agent-as-graph-templates、ADR-030 Event-Sourced Execution State)。1.3.19 落地的 `SUBGRAPH_START/END` 引用对 + log-as-truth WAL 已经覆盖 1.3.18 所需的全部运行时设施,**本 change 不引入新持久化、不引入新协议、不引入新执行原语**,只把现有 substrate 用足。本设计记录核心架构决策、关键算法和已知风险,具体规范见 `specs/dynamic-orchestration/spec.md`,动机见 `proposal.md`。

## Goals / Non-Goals

**Goals:**

- 让用户能用一条 `COORDINATOR` 节点配置表达"给目标 + 给 agent roster → 运行时产生 task DAG → dispatch → 合成"的端到端流程,不需要手画拓扑。
- 保证 plan 是**第一公民日志对象**:进入 `_plan` 通道默认被 LogPolicy 纳入日志,fold-to-version 可重建,SUBGRAPH_START/END 引用对天然嵌套子 session。
- 维持 6 个静态图模板与 1 个动态模式的**对称共存**:同一套 PregelRuntime、同一套 ChannelManager、同一套 EventStore、同一套 guardrail 中间件。
- 硬隔离父/子会话(5 条可测试断言),保证子任务的中间消息不污染父图。
- 失败可恢复:replan-with-carryover(replan 引用既有 task 输出),append-only 计划修订历史,Magentic 双循环图化。
- 资源有界:三轴预算 + additive `stop_reason`,capped 部分结果对 coordinator 可见地标注。

**Non-Goals**(本轮 v1 不做,推到 1.3.18a P4):

- 完整 consensus proposer→judge 形态(只做可选 per-task verifier hook)
- append-only PlanPatch repair API 的 outcome-barrier + `onPlanPatch` 审批(只做"replan 引用既有输出"退化形式)
- 异步编排 + 中途 steering(`update_async_task` / `cancel_async_task`,AgentScope inbox+wakeup 模式)
- plan 冻结 artifact + 精确重放(等 8.20 一起规划)
- UI 端(pattern-selector 第 7 模式、canvas COORDINATOR 节点、8.20 coordinator 卡片)
- 把 dynamic 模式加进 `build_graph_from_pattern`(v1 不允许静态声明 dynamic 图,dynamic 必须是运行时生成)

## Decisions

### D1:data-as-plan 而不是 code-as-plan(范式选择)

业界裂解为两范式:data-as-plan(OMA / Magentic / AgentScope / 企业平台 coordinator)和LLM / Deep Agents interpreters / Hermes / Claude Code 动态工作流)。Hecate 选 data-as-plan 理由:

- **多租户企业平台**:计划的"可审查 / 可回放 / 可治理"是硬需求,data-as-plan 天然是序列化数据;code-as-plan 的图灵完备代码无法序列化审查。
- **Pregel 免费提供确定性循环 / 分支 / 重试**:TaskDAG 的依赖结构本身就是循环展开的结果,executor 物化后由 BSP 循环确定性执行 —— 不需要解释器。
- **对齐 Hecate 既定承诺**:"所有编排都是图"(ADR-001 / ADR-007),TaskDAG 在物化后就是图,与 6 个静态模板同走 GraphCompiler → ChannelManager 路径。
- **唯一借鉴的 code-as-plan 能力**:synthesis 阶段支持声明式确定性 transform(Deep Agents "deterministic transforms"),但走 Pydantic 表达式而非 JS 沙箱。

**替代方案**:`CoordinatorWorker` 内嵌 JS quickjs 沙箱跑 plan 代码。否决:多租户下沙箱安全治理成本高(代码=任意副作用),且与 ADR-029 信任分级 Kernel 不兼容。

### D2:TaskDAG 作为 Pydantic 模型 + executor 物化(对接 GraphCompiler)

TaskDAG 字段:`goal` / `tasks: list[TaskNode]` / `dependencies: dict[id, list[id]]` / `synthesis_prompt` / `synthesis_transform` / `budgets: OrchestrationBudgets` / `verify: VerifyConfig | None`。**为什么不直接让 LLM 写 `GraphConfig`?**

- 业务术语不泄漏到 DSL;LLM 写 `TaskNode.agent_id` 即可,channel 名/节点 id 由 executor 物化时按 `agent_id + task_id` 自动生成。
- 让 LLM 写完整 DSL 风险大(节点 id 命名冲突、channel 名一致性),executor 物化时唯一化。
- Magentic-One 论文原话:"the plan serves more as a hint for step-by-step execution — neither the Orchestrator nor the other agents are required to follow it exactly",即 plan 是"建议性"而非"合同",executor 的物化决定执行形态。

**替代方案**:LLM 直接写 `GraphConfig` JSON。否决:稳定性、可调试性、企业控制面全部劣化。

### D3:Magentic 双循环的图化(Magentic arXiv 2411.04468 源协议)

```python
# 伪代码,实际伪代码
while iteration < budgets.max_iterations and not stalled():
    revision = await planner(goal, roster, ledger_summary)   # 新一轮 planner,无前轮中间推理
    if not validate_task_requirements(revision, roster):
        emit(ORCHESTRATOR_DECISION, dag=None, reasoning=verification_failed)
        continue
    emit(ORCHESTRATOR_DECISION, plan_revision=iter, dag=revision)
    sub_graph = build_dynamic_orchestration_executor(revision, roster)
    sub_session = uuid4()                                       # 独立 session_id
    SUBGRAPH_START(child_session=sub_session, ...)
    async for state in run_sub_graph(sub_graph, sub_session):
        forward_to_ledger(state)
    SUBGRAPH_END(child_session=sub_session, status=ok_or_failed)
    eval = await evaluator(ledger_summary)
    emit(ORCHESTRATOR_EVALUATION, verdict=eval.verdict, blocker=eval.blocker,
         stop_reason=..., guidance_string=...)
    if eval.verdict == "satisfied":
        return synthesize(ledger)
    if eval.verdict == "stalled":
        stall_counter += 1
        if stall_counter > budgets.stall_limit:
            emit(ORCHESTRATOR_DECISION, dag=None, reasoning=stall_cap_exceeded)
            return synthesize(ledger)  #  返回已有 best effort
```

**为什么 coordinator 的 prompt 每轮全新构建**(Magentic 论文原话:"we force all agents to clear their contexts and reset their states after each plan update"):Hecate 里 coordinator 是节点,每轮重新 dispatch 天然是新 invocation,只要 prompt 输入只包含 `goal + roster + ledger 摘要`,不灌上一轮的中间推理 token,即可享受同样的"清空上下文"红利。**不显式调 clear context,但通过输入约束实现等价语义**。

### D4:三轴预算 + additive stop_reason(DeerFlow v2.0 已交付,1:1 同源)

`OrchestrationBudgets` 字段:`max_iterations=5` / `max_total_tasks=6` / `max_concurrent=3` / `stall_limit=2` / `token_budget=None` + 任务边界检查 + 允许超一个 turn(OMA 同源)。

`stop_reason` 走 additive 字段(`token_capped | turn_capped | loop_capped | stall_capped`),**不引入新 EventType**,状态枚举保持稳定集。capped 部分结果在 `ORCHESTRATOR_EVALUATION.payload.guidance_string = "reuse it, retry tighter, or raise budget"` —— 模型可见指导,DeerFlow 同样模式。

**为什么不做 `status` 新枚举值**:DeerFlow Phase 1 已证实新枚举值会破坏 v1 消费者;additive 字段被老 frontend / ledger reader 忽略(向后兼容)。

### D5:benefit-based 委派 rubric(DeerFlow subagents/AGENTS.md 源协议)

Coordinator system prompt 内嵌 `benefit_based_delegation_rubric`(公开常量),指导 LLM:

- 默认直接执行;只在并行延迟 / 专家能力 / 上下文隔离收益**明显超过**启动 / 重复发现 / 综合 / 状态冲突 / 副作用成本时才派发;
- **硬否决**:并行波次内输出依赖、重叠可变状态;
- 用最少够用的任务数;后续每个波次重新评估;
- 用`subagents.max_total_per_run` / `max_concurrent` 的 clamp 值同时渲染进 prompt 和执行配置(模型看到的 = 实际执行的)。

rubric 文本由 `tests/test_coordinator_prompt.py` 的 snapshot 测试钉死,DeerFlow 同样模式(`test_subagent_routing_prompt.py`)。

### D6:五重隔离契约(DeerFlow subagents/AGENTS.md 1:1 同源)

子图相对父图的 5 条可测试断言:

1. **session 隔离**:独立 `child_session_id`,子图 checkpoint 不进父 restore 路径;
2. **记忆隔离**:子任务 agent 的长期记忆写入以子 session id 为键,绝不共享父 thread 键;
3. **输入隔离**:子图 `ChannelManager` 只注册 `channel_mapping.input` 显式声明的父通道,其他父通道 KeyError**(`ChannelManager.read` 对未注册通道抛 `KeyError`,此为既有行为,本 change 复用)**;
4. **输出隔离**:只回写 `channel_mapping.output` 显式声明的通道,子图 `messages` 通道**绝不**回写父图;
5. **失败判定权威**:只看 `WorkerResult.error` 和状态契约,**不解析子 agent 输出文本猜成败**(DeerFlow 原话:"only the marker is authoritative — error-looking assistant prose without it remains a normal completed result")。

### D7:可选 per-task verifier hook(补 decision-matrix D6 空白,OMA consensus 的轻量版)

`TaskNode.verify: {verifier_agent_id, prompt} | None`。执行顺序:primary worker 完成 → 若 `verify` 非空则调 verifier agent → verifier 返回 "PASS"/"FAIL" → 写 ledger 的 `verified` 字段 → `verified=false` 触发下一轮 `ORCHESTRATOR_EVALUATION.verdict = "stalled"`。**完整 consensus(proposer→judge 双向)** 留 1.3.18a。

### D8:fail-closed 前置校验(OMA v1.14 源协议)

`validate_task_requirements(dag, roster)` 在三个调用点统一行为:

1. **CoordinatorWorker 内部**:每轮 planner 输出后,validator 失败 → emit `ORCHESTRATOR_DECISION{plan_revision, dag=None, reasoning=verification_failed}` → 不执行该子图,等下一轮(允许 LLM 自纠);
2. **Canvas 保存时**:validator 暴露,前端可在编辑时提前发现配置错误;
3. **画布的 catalog 节点 +**:从 3 (agent-as-tool Pattern 2.3d ✅)+ Agent Templates 既有能力对接,模板可以预填 default roster。

校验四档 fail-closed(DAG 合法性 / 指派资格 / 能力不满足 / 引用悬挂)与 OMA v1.14 `validateTaskRequirements` 1:1 对齐。

### D9:EventType 两个新值 + LogPolicy 不豁免(ADR-030 §1 additive)

`EventType.ORCHESTRATOR_DECISION` 和 `EventType.ORCHESTRATOR_EVALUATION` 加进 `engine/eventstore.py`,**不豁免 LogPolicy**(默认入日志)。`payload` 形态:

- `ORCHESTRATOR_DECISION`:{`plan_revision`, `dag | None`, `reasoning`}
- `ORCHESTRATOR_EVALUATION`:{`verdict`, `blocker | None`, `stop_reason?`, `guidance_string?`}

typed blocker 走 7 分类(`satisfied` / `needs_user_input` / `missing_evidence` / `run_failed` / `external_wait` / `goal_not_met_yet` / `stalled`),DeerFlow 5 分类 + Magentic stall 状态扩展。未知 verdict 落 `stalled` + `blocker` 装原字符串,沿用 ADR-030 未知 EventType 回读落 `CUSTOM` 的兼容模式。

### D10:synthesis 节点声明式 transform(VARIABLE_SET 表达式复用)

TaskDAG 增字段 `synthesis_transform: str | None`,executor 在调 LLM 综合前先用既有 `VARIABLE_SET` 节点的表达式机制 evaluate 这个 transform,产出写到 `_synthesis_buffer` channel(只为 synthesis 节点可见)。**这是从 code-as-plan 阵营唯一保留的能力**(Deep Agents "deterministic transforms without another model turn"),但通过 Pydantic 表达式而非 JS 沙箱实现。

### D11:模型分离(planner / evaluator / workers)

Coordinator node config:`planner_model` / `evaluator_model`(默认小模型,DeerFlow "non-thinking evaluator" 同源)+ task 自身 roster agent 的 `model`。三段 LLM 调用走三段模型配置,**模型可见预算与执行预算同源**(同一个 clamp 函数同时渲染 prompt 和执行配置)。

## Risks / Trade-offs

- **R1:LLM 写 TaskDAG 的稳定性** → M:executor 物化时全部唯一化命名(节点 id = `task_<task_id>_<revision>` / channel id = `<task_id>.<expected_output>`),且 `validate_task_requirements` 前置 fail-closed 兜住 LLM 输出畸形。
- **R2:replan-with-carryover 引用既有输出可能引用已被 evict 的 channel** → M:`_ledger` channel 的 TTL 与 eviction 走既有的 ChannelManager 策略(参考 ADR-030 §8);carryover 引用若 KeyError,coordinator 转译为 `blocker = "upstream_evicted"` 并触发下一轮 replan。
- **R3:三轴预算的 token 计数精度** → M:同 OMA 边界检查语义,允许超一个 model turn;文档明确写出"token 预算是 circuit breaker 不是"不是账单";provider invoice 仍为成本真相。DeerFlow 同样模式(`token_budget.max_tokens` 与 `summarization.enabled` 联动)。
- **R4:rubric 文本快照测试可能因为误改 prompt 而误红** → M:rubric 抽到 `BENEFIT_BASED_DELEGATION_RUBRIC` 公开常量,snapshot 测试只对常量做 byte-compare,prompt 模板渲染逻辑的修改不会误红。
- **R5:verifier hook 引入新一轮 LLM 调用增加成本** → M:`verify` 字段可空;非空时 evaluator 默认用 evaluator_model(小模型)跑 verifier,不是 worker 的旗舰模型;verifier 输出只写 ledger,不进主消息流。
- **R6:HITL 穿透语义可能反直觉** → M:子任务 interrupt 默认走 1.3.19 `INTERRUPT` 事件透出,父图 `SUBGRAPH_END {status: interrupted}` 携带;coordinator 默认行为是"把 interrupt 当 `stalled` 触发 replan";`on_interrupt` config 字段允许升级到父级暂停(整场等审批),走 1.3.4 fail-closed approval。
- **R7:`event()` 自动驱逐旧 plan revision 的子图 channel** → M:每一轮新建子 session;`SUBGRAPH_END` 后子图的 channel 不进父图;若需要 carryover,executor 显式声明 `carryover_outputs: list[str]`,默认空。
- **R8:动态编排的可观测性现在没有 UI** → M(已登记):UI companion 是 follow-up change,不在本 change scope;但本 change 写入 EventStore 的事件对 8.20 回放透明,UI 后续直接消费。
- **R9:数据迁移** → 无,纯增量。1.3.19 之前已有的会话日志仍是 v1 schema,可重放;新事件类型未知值走 ADR-030 的兼容回读。

## Migration Plan

- **零迁移**:纯增量 feature,EventType additive,LogPolicy 默认入,无 schema 迁移。
- **部署**:与 1.3.19 / 1.3.4 并行;若 1.3.4 未上线,coordinator 节点的 `on_interrupt` 字段暂时禁用,走默认"中断当 stalled 重规划"语义。
- **回滚**:删除 `dynamic-orchestration` change 即可;不动既有的 6 个静态模式代码路径。

## Open Questions

无。所有可能改变 spec / design / tasks 拆分的决策均已在前序讨论中拍板(参见 /opsx:explore 阶段的 A1-A6 / B1-B7 / C1-C3 决策列表)。