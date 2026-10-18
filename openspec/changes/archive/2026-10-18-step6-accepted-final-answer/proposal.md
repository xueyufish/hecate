# Proposal

## Why

主干已交付已接受决策、动作集合串行恢复与单回合 `AgentTurn`,但端口响应方仍要求模型必须返回工具调用(空工具快照在模型调用前被拒绝,无工具调用响应在模型调用后显式失败),决策记录校验也要求动作集合非空——模型已经能直接给出最终回答时,当前单回合无法接受该结果。单回合因此只有"执行动作"一个出口,缺少"给出最终回答"出口,多轮 Controller 的停止分支缺位。这是演进方案 step5e/step6 明确记录的缺口(`PortDecisionResponder` 当前要求模型返回动作集合,不支持无工具的最终回答分支)。

## What Changes

- **显式区分两类决策**:已接受决策记录以 `schema_version` 区分动作决策(v1,非空动作集合,语义与摘要计算不变)与最终回答决策(v2,空动作集合且携带非空白回答文本);其他版本继续 fail-closed。历史记录不回填、不重新解释;v1 记录的存储形态、恢复行为与内容摘要原样保留。
- **端口响应方支持最终回答**:聚合结果无任何工具调用且含非空白文本时,产出动作集合为空的最终回答提案;工具定义快照为空时仍可进行一次模型调用,但只允许最终回答路径。空白最终输出、流中出现过工具调用却在最终聚合结果中消失、以及任何无效工具调用,SHALL 显式失败且不降级为成功回答。
- **复用既有接受与恢复链路**:最终回答决策经决策组件先查后问、同事务持久提交与内容摘要校验后由回合返回;恢复时直接命中已接受记录,模型零调用、动作零派发,文本与摘要保持一致。
- **回合出口显式化**:回合结果区分动作回合与最终回答回合;最终回答仅表示"已接受的模型输出",SHALL NOT 推进 Task 状态、SHALL NOT 判定任务成功或目标达成。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `accepted-agent-decision`: 新增决策记录显式版本语义(v1 动作决策 / v2 最终回答决策,校验与摘要按版本收口);端口响应方允许空工具快照下的最终回答路径与无工具调用的文本提案,并新增聚合消失保护;回合对最终回答决策零派发、出口显式暴露;恢复语义覆盖最终回答(零模型调用、零动作派发)。

## Impact

- `packages/hecate-runtime/src/hecate_runtime/`:`accepted_decision.py`(版本化校验与摘要不变性)、`port_decision_responder.py`(最终回答分支、流聚合保护)、`decision_step.py`(提案→记录的版本映射,如需)、`agent_turn.py`(出口显式化)。
- `packages/hecate-durable/`:SQL 与 InMemory 决策存储经共享校验路径获得 v2 支持;`durable_accepted_decision.schema_version` 列已存在,无 schema 迁移;接受事件的 `action_keys` 对最终回答为空列表(增量)。
- 测试:`packages/hecate-runtime/tests/` 与 `packages/hecate-durable/tests/` 新增最终回答分支用例,`tests/test_core/test_port_decision_responder_adapter.py` 验证生产端口的最终回答及计量;SQLite 与 PostgreSQL 均覆盖最终回答落库与跨进程恢复(证明恢复后模型调用与工具调用为零、文本与摘要一致)。
- 不改公开平台 API、Task 生命周期、入口路由、前端、共享 fixtures;不扩展到多轮 Controller、Plan Revision 或 Child Run。
