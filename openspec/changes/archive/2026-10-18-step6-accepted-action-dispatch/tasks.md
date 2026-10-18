# Tasks

## 1. 实现前核对

- [x] 1.1 核对 `ToolCallExecutor.execute_call` 的全部终态返回形状：`_withheld_result` 各调用点的消息内容、`ExecutionStoppedError` 的全部抛出点与 `reason_category` 值域、正常回执消息的字段，与 design D5 的控制流结果映射对齐；结论记录在实现备注
- [x] 1.2 核对 `SqlAcceptedDecisionStore._assert_owner` 的查询参数与异常语义，确定同一 SqlActionLedgerHook 内复用权威校验的方式（design D4）；不改存储行为
- [x] 1.3 复查账本恢复三路径（succeeded 回填、并发认领、未知停止）在现有测试中的替身写法，确定本 change 测试的 stub 组合方式

## 2. AcceptedActionStep 实现

- [x] 2.1 新建 `packages/hecate-runtime/src/hecate_runtime/accepted_action_step.py`：按 design D2 实现定位路径——直接 `store.lookup` + 身份/摘要/已提交校验，miss 与动作键不存在抛 `AcceptedActionLookupError`（区分两类），损坏/未提交抛 `DecisionStoreUnavailableError`，全程零模型调用；以"无决策/坏记录/无此动作键"零工具调用的测试通过为证
- [x] 2.2 按 design D3 实现派发身份：`tool_call` 由动作键与记录实际参数构造（拒绝调用方覆盖）、会话键从 `RunRef` 确定性派生（固定 namespace 的规范 JSON/UUID 映射，包含决策步骤），以跨进程重派发身份一致、随机回退不可达的断言通过为证
- [x] 2.3 由实际执行的 ActionLedgerHook 强制验证 Run/Task/真实 ownership；缺失或旧 hook 不支持时拒绝，领取和业务调用前复查；readonly 领取异常不得放行。
- [x] 2.4 从共享 executor 的执行控制流区分 succeeded/failed/withheld，ExecutionStoppedError 映射 stopped 并返回 execution_id，不由自由文本猜测。
- [x] 2.5 SqlActionLedgerHook 增加 accepted dispatch 绑定校验，在线程中复用权威租约/Task 关联；无 schema 或核心存储变更。

## 3. 测试

- [x] 3.1 新建 `packages/hecate-runtime/tests/test_accepted_action_step.py`，覆盖定位失败矩阵（无决策、损坏、未提交、存储不可用、动作键不存在、调用方覆盖参数）且每例业务工具零调用、模型零调用
- [x] 3.2 恢复三路径：stub 账本 + 真实 executor——重复派发回填真实结果零副作用、并发派发同一写动作至多一个首次执行者、已领取未知结果安全停止（stopped 携带 reason_category）
- [x] 3.3 跨进程身份：组件重建回归 + 两个真正独立 Python 子进程共用 SQL 决策/动作事实，执行身份一致并找回既有账本记录、模型零调用；策略拒绝与审批拒绝经注入 executor 的现有链路返回 withheld
- [x] 3.4 durable 桥测试：SQLite/PostgreSQL 真实决策存储 + 同一 SQL 动作 hook 的权威绑定校验，有效 owner 通过、过期/接管 owner 在业务调用前拒绝；既有 runtime_hook 测试无回归

## 4. 验证与收尾

- [x] 4.1 运行 `bash scripts/dev.sh test packages/hecate-runtime/tests/test_accepted_action_step.py packages/hecate-runtime/tests/test_decision_step.py packages/hecate-runtime/tests/test_tool_call_executor.py packages/hecate-durable/tests -q`，受影响用例通过，条件性跳过与新 PG 矩阵结果如实记录在 verification.md
- [x] 4.2 运行 `bash scripts/dev.sh check all`（ruff + format + mypy）达到 0 错误
- [x] 4.3 运行 `openspec validate step6-accepted-action-dispatch --strict` 通过，并逐条核对 delta spec 场景均有对应测试


## 5. 复核修正

- [x] 5.1 回归复现并修正上下文覆盖、Run 分隔符碰撞、不同步骤动作键碰撞和无账本无 owner 派发。
- [x] 5.2 强制同一 SQL hook 的 Task/Run/lease 绑定，补审批后租约失效（含 readonly）、错误 holder/token/Task/Run 及接管拒绝证据。
- [x] 5.3 新路径回执失败返回 stopped，业务失败区分 failed；不从外部消息回填结果，不允许 hook 静默改写已接受参数，旧入口行为保持。
- [x] 5.4 真实 SQLite/PostgreSQL 账本并发、结果复用与回执丢失，以及两个独立进程恢复验证；测试各自只清理自身数据库/schema。
- [x] 5.5 完成受影响回归、共享检查、严格 OpenSpec 校验并记录真实结果；不提交、推送或归档。
