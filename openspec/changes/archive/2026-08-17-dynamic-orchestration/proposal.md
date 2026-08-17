## Why

Hecate 当前 6 个 multi-agent 协作模式(`CollaborationPattern` 枚举下的 sequential / parallel / handoff / broadcast / negotiation / debate)全部是**预编译的图模板**,在编译期固定拓扑与 agent roster。但 2026 年业界已普遍支持 **runtime-emitted task DAG**:用户给一个 goal,LLM 在运行时拆出 task DAG 并 dispatch workers(Sprint 7 之前的 catalog 已点名 Magentic-One / Claude Code / OMA coordinator / AgentScope agent_spawn 作为参照)。在 2026-08-14 行业复核 + 2026-08-17 深度对标(deep-dive: Magentic-One arXiv 2411.04468 / OMA v1.14 / DeerFlow subagents/AGENTS.md / Deep Agents interpreters / AgentScope team/plan tools / dsh)之后,该缺口被定位为 **7th 多 pattern** —— 与 6 个静态模式并列的全新图原语,落地由 1.3.19(Event-Sourced Execution State)留出的 SUBGRAPH_START/END 引用对 + log-as-truth substrate 直接支持,**无需新持久化基础设施**。

## What Changes

- 新增 **`COORDINATOR` NodeType**(`engine/types.py`)+ `CollaborationPattern.DYNAMIC` 枚举值 + `infer_pattern()` 识别新形状。
- 新增 **`TaskDAG` 公共契约**(Pydantic model):`goal` + `tasks`(nodes) + `depends_on` + `expected_output` + `synthesis_prompt` + `budgets` + `verify` hook(可选);executor 模板把这张 DAG 物化成子图(独立 `child_session_id`)。
- 新增 **`CoordinatorWorker`**(`engine/workers/coordinator_worker.py`):走 `LLMWorker` 工具接口,持 planner model + evaluator model 分离配置。
- 新增 **`build_dynamic_orchestration_executor()`** 模板(`engine/templates.py`):TaskDAG → GraphConfig 的确定性物化器;产出子图根节点 + 同步 FAN_OUT + MERGE + 同步综合节点。
- 新增 2 个 **EventType**(`engine/eventstore.py`,additive,符合 ADR-030 §1):`ORCHESTRATOR_DECISION`{`plan_revision`,`dag`,`reasoning`} / `ORCHESTRATOR_EVALUATION`{`verdict`,`blocker`,`stop_reason?`};payload 的 typed blocker 走 5 分类(`missing_evidence` / `needs_user_input` / `run_failed` / `external_wait` / `goal_not_met_yet`,DeerFlow 同源);LogPolicy 默认入日志,不豁免。
- 新增 **`validate_task_requirements()`** 导出函数:派发前 fail-closed 前置校验(DAG 合法性 + 指派资格 + 能力不满足即拒绝,OMA v1.14 三档 fail-closed 同源)。
- 扩展 **synthesis 节点**:允许 `transform` 表达式(复用 `VARIABLE_SET` 节点的表达式机制),在做 LLM 综合前先做确定性聚合变换(借鉴 Deep Agents interpreters "deterministic transforms" 唯一保留动作)。
- 新增 **`benefit_based_delegation_rubric`** 与对应 prompt 快照测试(`tests/test_coordinator_prompt.py`):rubric 内嵌 coordinator system prompt,行为变更由测试钉死(DeerFlow `test_subagent_routing_prompt.py` 同源模式)。
- 新增 **per-task 可选 verifier hook**(`TaskDAG.verify` 字段):任务完成后、synthesis 前进 judge pass,补 decision-matrix D6 空白;完整 consensus(proposer→judge)留 1.3.18a。
- `engine/patterns.py` 与 `engine/workers/__init__.py` 同步导出;`openspec/specs/collaboration-pattern-engine/spec.md` 加 MODIFIED requirement(DYNAMIC 枚举)。

## Capabilities

### New Capabilities

- `dynamic-orchestration`:7th multi-agent pattern 完整契约 —— COORDINATOR 节点、TaskDAG schema、executor 物化、隔离断言、迭代协议、预算、verification、synthesis 变换、模型分离。

### Modified Capabilities

- `collaboration-pattern-engine`:`CollaborationPattern` 枚举加 `DYNAMIC` 值;`infer_pattern()` 新增识别分支(识别 COORDINATOR 节点 / TaskDAG 形状);`build_graph_from_pattern` 暂不加(v1 不允许静态声明 dynamic 图)。

## Impact

- **新增代码**:`engine/types.py`(NodeType 加 1 值)/ `engine/patterns.py`(enum + infer 分支)/ `engine/workers/coordinator_worker.py`(新建 ~150 行)/ `engine/templates.py`(加 `build_dynamic_orchestration_executor` ~150 行)/ `engine/eventstore.py`(EventType 加 2 值)/ `engine/dynamic_types.py`(TaskDAG Pydantic ~60 行)/ `engine/orchestrator_validator.py`(`validate_task_requirements`,~40 行)/ `openspec/specs/dynamic-orchestration/spec.md` 新文件。
- **修改代码**:`engine/workers/__init__.py`(导出)/ `engine/patterns.py`(枚举 + infer)/ `openspec/specs/collaboration-pattern-engine/spec.md`(MODIFIED requirement)/ `docs/features/feature-catalog.md` 与 `docs/features/roadmap.md`(已在 2026-08-17 scope-freeze 中落盘,无需再改)。
- **依赖**:2.7a ✅ + Pregel ✅ + 1.3.19 ✅(子 session + log-as-truth substrate 直接复用,plan 写通道即入日志);1.3.4 fail-closed approval(Sprint 7 并行,不属于本 change 依赖)。
- **零新持久化**:plan 进入 `messages` 通道或专用 `_plan` channel(LogPolicy 默认入),`SUBGRAPH_START/END` 由 1.3.19 已发事件承载,checkpoint 继续是 log 之上的可丢弃缓存。
- **零新基础设施**:不引入 MCP server、不引入新的 worker 类型注册路径、不引入 HTTP 长连接(异步路径 1.3.18a)。
- **推迟项(已登记到 catalog/roadmap)**:1.3.18a(P4)consensus proposer→judge、append-only PlanPatch repair API、异步编排 + 中途 steering、plan 冻结重放;UI companion(P3,follow-up change)挂 pattern-selector-ui / multi-agent-canvas / 8.20。