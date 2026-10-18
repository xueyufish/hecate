# Design

## 背景

`accepted-agent-decision` 栈现有三个具体组件：`DecisionStep`（先查后问，`execute()` 返回 `AcceptedDecisionOutcome{record, resumed}`，恢复命中零模型调用）、`AcceptedActionStep`（单动作治理派发，`dispatch()` 已接受可选 `expected_decision_digest` 并在记录不一致时抛 `DecisionConflictError`）、`AcceptedActionBatch`（串行批处理，`run()` 从自身 `load_verified_record` 固定摘要后逐动作传入）。三者的拼装目前完全留给宿主。

### 真实缺口：接受（T1）与批处理加载（T2）之间的摘要窗口

`AcceptedActionBatch.run()` 只在 T2 用自己查到的 `record.content_digest` 作为期望摘要。若记录在 T1（`DecisionStep` 提交）与 T2（batch 加载）之间被替换为另一份合法已提交记录（不同 `accepted_output`/`actions`，摘要不同），batch 会把新记录当作已接受事实派发——违背"派发的必须是模型提出且被接受的那份决策"的核心不变式。闭合该窗口要求把 T1 的摘要从回合入口强制传入批处理。

## 目标 / 非目标

**目标**：一个具体的单回合组合组件，固化"模型响应 → 决策提交 → 业务动作"顺序、跨阶段摘要绑定、ownership 贯穿与同入口恢复；批处理入口具备接受外部期望摘要的能力。

**非目标**：多回合循环、下一步调度、文本终答支持、Task 成功判定、入口切流、新存储接缝、新异常类型体系；不修改两个在途变更（`step11-agent-version-access-boundary`、`step14-task-command-query`）涉及的源文件。

## 关键决策

### D1：组合接线——构造时共享同一个 store

```python
class AgentTurn:
    def __init__(self, store: AcceptedDecisionStore, responder: DecisionResponder,
                 executor: ToolCallExecutor) -> None:
        # 内部组合：DecisionStep(store, responder)
        #           AcceptedActionStep(store, executor)
        #           AcceptedActionBatch(step, store)
```

三个内部组件共享同一个 `AcceptedDecisionStore` 实例。备选方案：让调用方分别构造三个组件再注入——被否决，因为这重新打开"决策与批处理各查各的存储"的错配空间，也把组合正确性责任推回宿主。内部组件不做参数化替换（不新增接缝），符合"无第二实现不新增扩展点"的仓库规则。

### D2：摘要贯穿——T1 摘要显式传入批处理，批处理入口新增可选参数

`AcceptedActionBatch.run()` 新增可选参数 `expected_decision_digest: str | None = None`；提供时，在 `load_verified_record` 之后、动作循环之前校验 `record.content_digest == expected_decision_digest`，不一致抛 `DecisionConflictError`。

放置位置与既有先例一致：`load_verified_record`（第 79 行）本身就在 try 块之外，预派发阶段的存储类失败（`AcceptedActionLookupError`、`DecisionStoreUnavailableError`）从 `run()` 直接抛出；摘要冲突同为"进入派发前的记录级失败"，同样直接抛出，而**不是**像派发中途的 `DecisionConflictError` 那样被转换为首个动作的 `stopped` 结果——此时还没有任何动作被处理，不存在可保留的"已完成前缀"，转换反而会伪造一个未派发动作的停止事实。

`AgentTurn` 取 `DecisionStep` 返回的 `outcome.record.content_digest` 传入。不传时批处理保持既有内部固定语义（规范以场景固定此兼容性）。

### D3：异常策略——零新增异常类型

回合的全部失败模式已有类型：输入版本冲突（`DecisionConflictError`）、提交失败/损坏/存储不可用（`DecisionStoreUnavailableError`）、摘要冲突（`DecisionConflictError`）、fencing 失效（`StaleDecisionFenceError`）、批内动作停止（`stopped` 结果）。`AgentTurn` 不包装、不吞并、不翻译这些异常，直接传播；新增错误类型体系属于"无第二消费者的新抽象"，违反仓库规则。批处理内部的既有转换规则（派发中途治理拒绝 → `stopped` 并保留已完成前缀）原样生效，回合不重复实现。

### D4：结果边界——`AgentTurnOutcome` 只携带事实

```python
@dataclass(frozen=True)
class AgentTurnOutcome:
    record: AcceptedDecisionRecord      # 已接受决策原记录
    resumed: bool                       # 决策阶段是否查找命中
    report: AcceptedActionBatchReport   # 逐动作结果 + 未处理动作键
```

不推断完成、不推进 Task、不触发重规划；"是否完成"是宿主的判断，不是回合的输出。批处理报告语义原样透传。

### D5：ownership 贯穿

`AgentTurn.run(ownership=...)` 把同一 `DecisionOwnership` 实例同时传给 `DecisionStep.execute()` 与 `AcceptedActionBatch.run()`（后者转传每个动作的 `dispatch()`）。未携带时按各组件既有约束处理（需要租约处显式失败），回合不代签、不补造、不阶段间更换。

### D6：入参必须先于决策阶段校验

AgentTurn 在任何 lookup/响应方/commit 前拒绝不是 dict/None 的 context 与 execution_context，以及非 None 但不是 DecisionOwnership 的 ownership。原代码对空列表/False 会沿下游 truthiness fallback 当空上下文执行，非空列表则提交后才 TypeError。前置类型校验避免成本与接受事实已落地后才发现无法派发；不复制含宿主对象的上下文、不重新实现 lease 权威校验。input_refs 与 step 标识继续由 DecisionStep 在存储前验证。

### D7：命名

组件名 `AgentTurn`，模块 `agent_turn.py`。备选 `AgentTurnStep`/`AcceptedAgentTurn`：同族 `DecisionStep`/`AcceptedActionStep`/`AcceptedActionBatch` 以"处理什么"命名，本组件组合的是"一个回合"，`AgentTurn` 语义直接且不与 Step/Batch 族混淆；它不是扩展点（无 ABC、无第二实现），不适用 `*Port`/`Hecate*Adapter` 规则。

## 测试策略

- **runtime 单元层**（`packages/hecate-runtime/tests/test_accepted_agent_turn_runtime.py`，新文件，复用既有 stub 模式：stub store/responder/executor/ledger hook）：固定顺序与提交失败零业务调用；摘要绑定（fresh commit 与恢复命中两种决策到批处理窗口替换记录 → `DecisionConflictError` 且派发零次；批处理收到期望摘要）；批处理缺省摘要的向后兼容；ownership 同实例贯穿与未携带时显式失败；恢复命中响应方零调用；输入版本冲突零模型调用；结果透传与无完成性推断。
- **durable 独立进程层**（`packages/hecate-durable/tests/test_accepted_agent_turn.py` + `_accepted_agent_turn_process.py`，新文件，真实 `SqlAcceptedDecisionStore` + `SqlActionLedgerHook`，复用 `_decision_helpers.py`）：进程探针两阶段——阶段一提交决策并派发部分动作后 `os._exit` 硬退出；阶段二经同一入口恢复，断言响应方零调用、已完成动作账本回填零副作用、剩余动作继续执行。SQLite/PostgreSQL 矩阵沿用 #280 模式。
- **实际验证环境**：本地 hecate-step6-pg 提供宿主端口 55432，复核可使用独立临时数据库跑 PG 矩阵；本 worktree 初始未安装 psycopg，经声明的 locked postgres profile 同步后实跑。不得把数据库未配置或驱动缺失当作通过，也不触碰并行 worktree 的数据库。

## 风险与权衡

- **批处理签名扩展的兼容性**：`expected_decision_digest` 为可选参数，缺省路径行为不变；规范以专门场景固定。风险低。
- **回合入口可能被误用为完成判定器**：规范与 docstring 明确结果边界；不做超出报告透传的语义。
- **与在途变更的合并冲突**：本变更全部落点为新文件（`agent_turn.py`、三个新测试文件）+ `accepted_action_batch.py` 单点小扩展；step11 尚无代码，step14 只改 durable 三个源文件，无交集。


## 复核验证补强

- 在实际业务调用内部断言原决策已提交并核验阶段事件顺序，而不是仅在回合结束后检查 accepted_at。
- 独立进程恢复输出原 record 和逐动作报告，核对完整原记录、稳定 execution_id 与业务结果载荷；保留原执行器首跑字符串化/SQL 恢复 JSON 化的兼容语义。
- receipt-loss 进程令 A2 实际写入效果但 outcome 提交失败；recover-unknown 新进程禁止所有工具/模型调用，回填 A1、保持 A2 needs_review 并阻止 A3；Task 状态不变。
- 真实 SQL 验证 commit 后首动作前租约到期、以及 ownership=None 不能提交决策；不以宽松内存 stub 的成功代替持久租约证据。
- 取消和 ApprovalWaitSignal 经真实审批回调抛出，回合不转换异常、不派发后续动作；无新的完成语义或自动重试。
