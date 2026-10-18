# Proposal

## Why

主线已分别具备 `DecisionStep`（先查后问的决策接受）、`AcceptedActionStep`（单动作治理派发）与 `AcceptedActionBatch`（串行批处理与恢复），但三者仍由宿主自行拼装。自行拼装无法保证"模型响应 → 决策提交 → 业务动作"的顺序不变式，且存在一个真实的摘要窗口：批处理入口目前只在自身加载记录时固定摘要，若记录在决策提交（T1）与批处理加载（T2）之间被替换为另一份合法已提交记录，宿主拼装路径会把新记录当作已接受事实继续派发。需要一个统一的单回合组合入口，把跨阶段摘要绑定与恢复语义固化在一处。

## What Changes

- 新增具体组合组件 `AgentTurn`（`packages/hecate-runtime/src/hecate_runtime/agent_turn.py`）：一次调用只处理指定 Run 引用与 `decision_step_id`，内部按固定顺序组合 `DecisionStep`（查找或接受决策）与 `AcceptedActionBatch`（串行派发原动作集合），构造时注入同一个 `AcceptedDecisionStore`、既有 responder 与已配置治理链的 executor。
- 决策阶段与动作阶段绑定同一份已接受决策摘要：回合入口取得 `DecisionStep` 返回记录的 `content_digest` 并强制传入批处理，批处理在派发前校验记录未被替换，摘要不一致时以显式冲突失败且业务调用为零。
- **扩展 `AcceptedActionBatch.run()`**：新增可选 `expected_decision_digest` 参数，在加载已验证记录后、任何派发前做 fail-closed 摘要校验；不传时保持既有内部固定语义，既有调用方不受影响。
- 入口前置校验 context/execution_context 的 dict/None 类型及非空 ownership 的 DecisionOwnership 类型；错误输入在任何决策读取、模型调用或提交前拒绝，不把空列表/False 当作空上下文。ownership=None 的既有租约约束保持。
- 返回组合结果：已接受决策记录、是否为恢复命中（`resumed`）、以及逐动作批处理报告（含未处理动作键）；恢复与新回合经由同一入口，恢复路径由 `DecisionStep` 查找命中保证模型零调用。
- 测试：runtime 测试目录新增组合语义测试；durable 测试目录新增 SQLite/PostgreSQL 独立进程验证测试与进程探针（沿用 #280 的 `_accepted_action_batch_process.py` 模式）。
- 明确不做：多回合循环、下一步调度、文本终答支持、Task 成功判定、入口切流、新存储接缝；不修改 `step11-agent-version-access-boundary` 与 `step14-task-command-query` 两个在途变更涉及的源文件。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `accepted-agent-decision`: 增补回合级组合要求——单回合入口的固定阶段顺序、跨阶段决策摘要绑定、ownership 贯穿两阶段、恢复同入口模型零调用、组合结果的显式边界（不推断完成、不推进 Task）。

## Impact

- `packages/hecate-runtime/src/hecate_runtime/agent_turn.py`（新增）
- `packages/hecate-runtime/src/hecate_runtime/accepted_action_batch.py`（扩展：可选 `expected_decision_digest` 参数）
- `packages/hecate-runtime/tests/`（新增组合语义测试文件）
- `packages/hecate-durable/tests/`（新增独立进程验证测试与探针文件，不修改既有文件）
- 不修改平台 Task 查询/API、evaluation、schema、conftest、演进计划、功能清单，不触碰 shim 层（accepted-decision 栈无 shim 转发模块）。
