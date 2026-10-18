# Tasks

## 1. 批处理入口扩展

- [x] 1.1 `accepted_action_batch.py`：`run()` 新增可选参数 `expected_decision_digest: str | None = None`；提供时在 `load_verified_record` 之后、动作循环之前校验 `record.content_digest`，不一致抛 `DecisionConflictError`（不转换为 `stopped` 结果）；缺省时行为与现状完全一致
- [x] 1.2 确认既有批处理测试（`packages/hecate-runtime/tests/test_accepted_action_batch_runtime.py`、`packages/hecate-durable/tests/test_accepted_action_batch.py`）在未传新参数时全部原样通过（回归，不修改这两个文件）

## 2. 回合组合组件

- [x] 2.1 新增 `packages/hecate-runtime/src/hecate_runtime/agent_turn.py`：`AgentTurn(store, responder, executor)` 内部组合 `DecisionStep`/`AcceptedActionStep`/`AcceptedActionBatch`，三组件共享同一 store 实例；英文 docstring 说明单回合边界与结果语义
- [x] 2.2 `AgentTurnOutcome` 冻结 dataclass：`record`（已接受决策原记录）、`resumed`（决策阶段是否查找命中）、`report`（批处理报告）；不含任何完成性判断字段
- [x] 2.3 `AgentTurn.run(run_ref=..., decision_step_id=..., input_refs=None, ownership=None, context=None, execution_context=None)`：同一 `ownership` 实例贯穿 `DecisionStep.execute()` 与批处理；取得 `outcome.record.content_digest` 强制传入批处理；异常零包装直接传播；入参做与栈内组件一致的类型/非空校验

## 3. runtime 单元测试

- [x] 3.1 新增 `packages/hecate-runtime/tests/test_accepted_agent_turn_runtime.py`（复用既有 stub 模式，不修改既有测试文件），覆盖：
  - [x] 3.1.1 新回合固定顺序：响应方先于任何业务调用；决策提交成功后按已提交顺序派发
  - [x] 3.1.2 决策阶段失败（提交失败/存储不可用/输入版本冲突）时业务工具零调用、账本零认领
  - [x] 3.1.3 摘要绑定：决策提交后、批处理加载前替换为另一份合法已提交记录 → `DecisionConflictError` 且派发零次；断言批处理收到决策阶段摘要
  - [x] 3.1.4 批处理缺省摘要：不传 `expected_decision_digest` 时既有语义不变
  - [x] 3.1.5 ownership：同一实例贯穿两阶段；未携带时按既有约束显式失败，不代签
  - [x] 3.1.6 恢复：预置已提交记录后发起回合，响应方零调用；输入版本不一致时 `DecisionConflictError` 且响应方零调用
  - [x] 3.1.7 结果边界：`AgentTurnOutcome` 透传 `resumed` 与批处理报告（含未处理动作键），无完成性语义

## 4. durable 独立进程验证

- [x] 4.1 新增 `packages/hecate-durable/tests/_accepted_agent_turn_process.py` 进程探针：阶段一提交决策并按参数派发前 N 个动作后 `os._exit` 硬退出；阶段二经同一入口恢复并输出断言结果（沿用 #280 `_accepted_action_batch_process.py` 模式）
- [x] 4.2 新增 `packages/hecate-durable/tests/test_accepted_agent_turn.py`：真实 `SqlAcceptedDecisionStore` + `SqlActionLedgerHook`（复用 `_decision_helpers.py`），SQLite/PostgreSQL 矩阵覆盖：
  - [x] 4.2.1 提交后、首动作前硬退出，新进程恢复：响应方零调用、动作从头派发
  - [x] 4.2.2 A1 完成后硬退出，恢复回填 A1 真实结果（业务工具零调用、无重复副作用）后执行 A2
  - [x] 4.2.3 恢复路径替换记录/篡改摘要 → 显式冲突失败且业务调用为零

## 5. 验证与收尾

- [x] 5.1 `pytest packages/hecate-runtime/tests/test_accepted_agent_turn_runtime.py packages/hecate-runtime/tests/test_accepted_action_batch_runtime.py -q` 全绿
- [x] 5.2 durable SQLite 半区：`pytest packages/hecate-durable/tests/test_accepted_agent_turn.py -q`（PG 半区如本地不可达，如实记录并交由 CI）
- [x] 5.3 相关回归：`pytest packages/hecate-runtime/tests/ -q` 与 `pytest packages/hecate-durable/tests/ -q` 全绿（确认批处理扩展未破坏既有语义）
- [x] 5.4 自充分性检查：`agent_turn.py` 仅依赖 `hecate_runtime` 包内模块，无平台/跨域导入
- [x] 5.5 共享静态检查：`bash scripts/dev.sh check all` 零错误（ruff + format + mypy）
- [x] 5.6 `openspec validate step6-accepted-agent-turn --strict` 通过；任务完成即实时勾选本文件


## 6. 复核修正

- [x] 6.1 前置校验错误上下文与 ownership 类型；新增回归先复现 5 个失败再修复；保持 ownership=None 的既有语义。
- [x] 6.2 补直接观察提交顺序、fresh commit 摘要替换、取消与持久审批等待信号透传断言；不修改既有批/单动作测试。
- [x] 6.3 独立进程核对完整原记录、结果载荷与稳定身份；A2 回执丢失后新进程不重做，Task 状态不推进；真实 SQL 验证提交后租约到期与缺 owner。
- [x] 6.4 实跑 SQLite/PostgreSQL 矩阵、完整相关回归和共享检查，保存 verification.md/review.md 的实际结果与跳过项。
