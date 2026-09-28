# Proposal

## Why

G2 门槛(方案 §二"尚未关闭的实施门槛",基线文档已登记本 change 为责任 change):工具恢复判定只看"有没有 `TOOL_RESULT` 回执",存在四个已复核的缺口——`TOOL_CALL` 与 `TOOL_RESULT` 之间的崩溃窗口被当作"从未开始"放行重跑、事件存储读取失败被当作"无回执"降级放行(fail-open)、成功恢复返回 `[recovered]` 占位文本而非真实结果、`failed` 回执对非幂等写仍自动重跑而异常类型并不能证明副作用未发生。方案 G2 行与第八节验收矩阵要求 step1 先补"安全停止语义":注入"写入成功→结果落盘前崩溃"、读库失败、重复 worker、同键不同参数时,证明无盲目重复写。

前置:#184(基线文档)与 #185(G1 MCP/REST 动作授权)已合并;本 change 无其他在途依赖。

## What Changes

- 恢复判定从"查 TOOL_RESULT 有无"扩展为四态查询:`never_started` / `claimed` / `outcome_unknown` / `store_unavailable`;`succeeded` / `failed` 终态保留在回执上
- `TOOL_CALL` 事件升格为领取记录:恢复查询与领取落盘在 `acquire_event_lock` 锁区内完成,重复 worker 并发派发同一动作键时后到者看到 `claimed` 并安全停止(写操作永不因锁超时回到自动重跑)
- `TOOL_CALL` / `TOOL_RESULT` payload 增加 `arguments_digest`(canonical JSON 的 SHA-256);同一 `execution_id` 下参数摘要变化 → 显式冲突拒绝,不执行、不区分工具类别
- 成功恢复从通道历史回填**真实结果**,移除 `"[recovered] ..."` 占位文本;结果内容确实不可恢复时返回显式"待对账"标记(附 `result_digest` 供后续核验),不伪造恢复成功
- 事件存储读取失败(`store_unavailable`)对写操作 fail-closed:仅 `readonly` 放行,`idempotent_write` 及以上全部安全停止,不再降级为"无回执"
- **行为收紧(翻案)**:`should_auto_retry` 矩阵中 `non_idempotent_write` / `external_side_effect` / `unknown` 类在 `failed` 回执下不再自动重跑,改为人工核对;依据是方案 G2"执行后异常不能仅凭异常类型判定无副作用;只能在明确幂等保证或已对账后重试"。`test_retry_decision_matrix` 钉死的旧行为将随之修改
- `InMemoryEventStore` 补 `acquire_event_lock` 真实实现(asyncio 锁);`PostgresEventStore` 锁与领取语义经 EventStore 契约测试确认

明确不做(留待 step6/7 持久化 Action 闭环):平台 Action 持久表、受 ACL 保护的回执结果引用存储、跨后端回执关联、对账工作流与补偿关联。

## Capabilities

### New Capabilities

(无——全部语义落在既有 `tool-recovery` 能力内)

### Modified Capabilities

- `tool-recovery`:四个 Requirement 均受影响。(1)"工具执行回执与稳定调用 ID"中"不存在未配对 `TOOL_CALL`"的不变式改为领取语义(崩溃窗口内 `TOOL_CALL` 无 `TOOL_RESULT` 是合法的 `claimed` 态),并要求记录 `arguments_digest`;(2)"副作用分类与重试判定"的 `failed` 重试规则收紧;(3)"恢复流程消费工具回执"增加四态判定、真实结果回填、`store_unavailable` fail-closed 与占位文本禁令;(4)新增"领取原子性与参数摘要冲突"要求(原 spec 未覆盖并发领取与参数变化)

## Impact

- `src/hecate/runtime/workers/tool_worker.py`:恢复判定重写(`get_tool_receipt` → 四态状态查询)、锁区内领取、digest 记录与冲突拒绝、成功恢复改走通道历史
- `src/hecate/runtime/tool_side_effects.py`:重试矩阵收紧
- `src/hecate/runtime/eventstore.py`(`InMemoryEventStore` 锁实现);`src/hecate/studio/event_state/postgres_store.py`(锁语义契约确认,预期无 schema 变更)
- 测试:`tests/test_runtime/test_tool_receipts.py`(矩阵翻案 + 恢复语义)、`tests/test_runtime/contracts/test_event_store_contract.py`(领取原子性进契约)、新增方案点名的四个故障注入用例
- 治理关联:方案 `docs/research/enterprise-agent-platform-evolution-plan.md` §二 G2 行、§八验收矩阵前四行;`docs/research/platform-evolution-baseline.md` G2 门槛记录;完成后 step6/7 的持久化 Action 闭环以本 change 语义为基座
