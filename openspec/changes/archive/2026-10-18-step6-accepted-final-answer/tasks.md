# Tasks

## 1. 决策记录版本化校验(Runtime 契约)

- [x] 1.1 在 `packages/hecate-runtime/src/hecate_runtime/accepted_decision.py` 的 `validate_decision`/`canonical_decision_digest` 路径实现 v1/v2 版本化校验:v1 保持非空动作集合与既有规则;v2 要求空动作集合且 `accepted_output` 含非空白字符串 `text`;其他版本显式失败;摘要计算不变(验证 v1/v2 摘要互异)。验证:`packages/hecate-runtime/tests/` 新增记录级用例(合法 v2 落库形状、空白文本拒绝、v2 带动作拒绝、空动作 v1 拒绝、未知版本 fail-closed、v1 全部既有断言不变)全部通过。
- [x] 1.2 为 `DecisionProposal` 增加显式 `kind` 判别(缺省 `actions`,合法值 `actions`/`final_answer`),非法值显式失败。验证:proposal 级单元用例通过;既有构造 `DecisionProposal(accepted_output=..., actions=...)` 的测试文件不改仍通过。

## 2. 端口响应方最终回答分支

- [x] 2.1 修改 `packages/hecate-runtime/src/hecate_runtime/port_decision_responder.py`:移除 `respond()` 对空工具快照的无条件拒绝;聚合新增跨块 `saw_tool_calls` 标志与消失保护(任一块出现过工具调用而最终聚合无工具调用 → 显式失败);最终聚合无工具调用且文本非空白 → 产出 `kind="final_answer"`、空动作集合的提案;空白最终输出显式失败;合法最终回答的 usage 仍经 `on_usage` 透传一次。验证:`packages/hecate-runtime/tests/test_port_decision_responder.py` 新增用例——纯文本最终回答、空快照 + 纯文本、空快照 + 任何工具调用显式失败、空白输出失败、消失保护(模拟先工具调用后纯文本的流)、既有动作分支全部断言不变。
- [x] 2.2 确认带工具快照下"文本 + 工具调用"混合响应仍产出动作提案(v1 语义),文本为可见输出。验证:既有混合响应用例继续通过。

## 3. 接受与恢复链路(DecisionStep / AgentTurn)

- [x] 3.1 在 `packages/hecate-runtime/src/hecate_runtime/decision_step.py` 将提案 `kind` 映射为记录 `schema_version`(actions→1、final_answer→2)并做提交前一致性校验(`final_answer` ⇒ 空动作 + 非空白文本;`actions` ⇒ 非空动作);恢复命中路径经版本化 `validate_decision` 自然支持 v2,无需分叉。验证:`packages/hecate-runtime/tests/test_decision_step.py` 新增用例——v2 提交后原样返回、v2 恢复零模型调用(stub responder 计数为零)、不一致 kind/内容组合显式失败。
- [x] 3.2 在 `packages/hecate-runtime/src/hecate_runtime/agent_turn.py` 为 `AgentTurnOutcome` 增加由记录派生的只读判别属性(如 `is_final_answer`),不改构造签名;最终回答回合零派发由批处理空循环自然达成。验证:`packages/hecate-runtime/tests/test_accepted_agent_turn_runtime.py` 新增用例——最终回答回合返回文本 + 空报告 + 零工具执行(executor stub 零调用)、判别属性正确、既有动作回合约断言全部不变。

## 4. 持久化与跨进程恢复(Durable)

- [x] 4.1 扩展 `packages/hecate-durable/tests/` 参数化契约套件(SQL 与 InMemory):v2 最终回答的原子提交、幂等重投、内容冲突、fencing、损坏记录与未知版本 fail-closed;确认 `agent_decision_accepted` 事件 payload 对 v2 携带空 `action_keys`。验证:套件双实现全部通过。
- [x] 4.2 扩展 `packages/hecate-durable/tests/test_accepted_decision_pg.py`:PostgreSQL 下 v2 最终回答落库、独立进程查找返回相同文本/摘要/空动作集合。验证:PG 用例通过(未配置 PG 的跳过按既有约定显式记录,不计为通过)。
- [x] 4.3 扩展跨进程恢复用例(`_accepted_agent_turn_process.py` 模式):提交 v2 最终回答后进程退出,新进程经同一回合入口恢复——模型调用计数为零、工具调用计数为零、文本与内容摘要与首次提交一致。验证:SQLite 与 PostgreSQL 进程矩阵用例均通过。

## 5. 回归与变更验证

- [x] 5.1 运行受影响测试全集:`python -m pytest packages/hecate-runtime/tests packages/hecate-durable/tests -q` 全绿;再按 `bash scripts/dev.sh test` 跑受影响范围确认无外部回归(Runtime 公开导出新增不影响既有导入)。验证:命令输出 0 failed。
- [x] 5.2 运行 `openspec validate step6-accepted-final-answer --strict` 与适用共享检查(ruff/mypy 于改动文件);确认无 schema 迁移、无新增依赖声明需求。验证:命令全部通过,`git diff` 不含 alembic/依赖清单变更。


## 6. 审查修正与补验

- [x] 6.1 修正空 `tool_calls` 列表的误拒，保留非空调用被清空时的失败保护；非法 proposal kind 类型统一抛 DecisionValidationError；新增 v1 固定摘要和历史 opaque output 兼容测试。
- [x] 6.2 扩展 v2 真实 ownership/fencing、outbox 回滚、文本冲突及损坏/未知版本恢复测试；新增提交后真实进程崩溃并恢复的 SQLite/PostgreSQL 场景及 Task 状态不变断言。
- [x] 6.3 增加生产端口纯文本最终回答用例，覆盖空/非空工具快照与一次用量结算；同步设计和 delta spec。
- [x] 6.4 运行 Runtime/Durable 完整包测试、PostgreSQL 相关矩阵、生产适配器及边界回归、共享检查和 OpenSpec strict validation，记录实际结果。
