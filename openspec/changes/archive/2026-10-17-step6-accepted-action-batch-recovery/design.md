# Design

## Context

#277（`accepted-action-step.py` + `accepted_decision.py` + durable 桥）交付了单动作安全执行与恢复：`AcceptedActionStep.dispatch(run_ref, decision_step_id, action_key)` 经 `AcceptedDecisionStore.lookup` 读取持久记录、校验归属、从有序 `record.actions` 定位动作、以 `session_key_for_run`/`tool_call_id_for_action`/`execution_id_for_action` 派生稳定身份、经 `ToolCallExecutor.execute_accepted_call` 走受治理链；结局为四值 `AcceptedActionOutcome`（`succeeded`/`failed`/`withheld`/`stopped` + `stop_reason`），`ExecutionStoppedError` → `stopped`。`AcceptedDecisionRecord.actions` 本身就是持久化有序动作集。durable 侧已有 `accepted_action_process.py` 跨进程 harness 与 bridge/PG/store 测试基建。

## Goals / Non-Goals

**Goals:** 一个批处理组件：加载已提交决策 → 按持久顺序逐个调用既有单动作派发 → 终态即停 → 返回逐动作结局与未处理键；恢复与首跑同路径（账本回填即恢复）；跨真进程与双库验证。

**Non-Goals:** 多轮 Controller、并行动作调度、自动重规划、入口切流、Task 成功推进、新表/迁移、批级别前置检查替代逐动作边界。

## Decisions

### D1: 批处理是纯编排，恢复语义完全寄生于单动作派发

`AcceptedActionBatch.run(run_ref, decision_step_id, ownership=..., context=...)`：`store.lookup` 取记录 →（空记录/校验失败按既有错误透传）→ 固定已校验记录的 content_digest，按 `record.actions` 顺序循环 `step.dispatch(..., expected_decision_digest=...)` → 聚合 `AcceptedActionOutcome` 列表；遇 `status != "succeeded"` 即 break。**恢复不需要专门代码**：对已完成 A1 重新 dispatch，账本按相同 execution id 回填真实结果（`succeeded` 携带原结果、工具零调用）；对结果未知的 A2 重新 dispatch，账本按停止处理——"不盲目重做"由派发语义自然成立。批处理自身不记游标、不写新持久状态。

备选——批处理自建"已处理游标"持久化——被否决：账本已是事实源，双写游标引入一致性负担且无查询需求。

### D2: 入口只收 (run_ref, decision_step_id)，动作集不可注入

签名不暴露动作列表参数——动作与顺序只能来自 `AcceptedDecisionStore` 记录（spec 场景"调用方不能替换或裁剪"由构造保证，而非运行时校验拒绝）。ownership/context 透传给每个 `dispatch`，批处理不缓存、不改写。

### D3: 终态即停，返回值诚实

循环内 `outcome.status in {"failed", "withheld", "stopped"}` → 停止。返回 dataclass：`outcomes: tuple[AcceptedActionOutcome, ...]`（已处理者，含回填结果）+ `unprocessed_action_keys: tuple[str, ...]`（记录顺序中未派发者）。不推进 Task、不触发重规划——批结果与 Task 生命周期的衔接归后续 Controller 工作。

### D4: 单动作接口零破坏，公共校验按需提炼

`AcceptedActionStep.dispatch` 增加可选 expected_decision_digest；既有调用不传该参数时语义保持不变，批处理必须传入首次已校验摘要，在调用 executor 前拒绝另一份有效但不同的决策。若批处理需要"记录校验 + 动作定位"的复用（`_verified`/动作查找逻辑），提炼为模块级函数供两处调用；提炼以既有单动作测试零改动为界。load_verified_record 在 lookup 前验证 Run 与 step 身份，非法身份不得访问存储。

### D5: 验证矩阵映射关键验收

- A1 落盘后进程退出 → durable harness `accepted_action_process.py` 真双进程：进程一跑批至 A1 后 kill，进程二重入批处理，断言 A1 回填、A2 执行、A3 依场景、底层工具 A1 恰一次、模型零调用。
- 未知结果 → stub 执行器让 A2 账本落停止/未知，断言 A3 零派发 + 二次恢复仍停止。
- 租约过期/接管 → 过期 ownership 传入 → A2 拒绝停止。
- 双进程竞争同一写动作 → 真双进程并发批处理，断言首次执行恰一次（账本 fencing）。
- 双库：SQLite 与 PostgreSQL 各跑同一集成场景（既有 bridge/PG 测试模式）。

### D6: 明确错误边界，保留原有控制信号

首次加载缺失、损坏或不可用，按原异常透传，业务零调用；已开始批处理后发生错误则保留已处理前缀，将当前动作标为 stopped，并按原因区分：decision_conflict、decision_store_unavailable（含原记录消失）、stale_ownership、invalid_action。后续动作不派发，不能把存储故障称为授权拒绝。仅 succeeded 可以继续；CancelledError 和 ApprovalWaitSignal 不捕获、不改写。原单动作返回的 needs_review / reconciliation_required 等类别保持原值。

### D7: 真进程竞争与换主恢复证据

race-first 在 A1 业务效果 fsync 后、账本 outcome 提交前写 ready 标志并等待 release。父测试启动 race-second 独立解释器，断言同一 A1 的稳定身份、needs_review、A2/A3 未派发，效果文件只有 A1 一行。释放首进程后完成其余动作，再启动 recover-all 独立解释器，禁止一切工具/模型调用并核对三个账本结果。另验旧租约到期后使用新 holder/token 恢复 A1 并继续 A2/A3。每例 SQLite 文件/PG schema 均隔离。

## Risks / Trade-offs

- [串行处理期间 ownership 可能跨动作到期] → ownership 校验逐动作发生（D 纪律），租约过期自然拒绝后续动作——这正是验收场景而非缺陷；批处理不主动续租（续租归调用方/durable 层既有机制）。
- [恢复语义依赖账本回填的幂等性] → 该幂等性是 #277 已验收事实（单动作恢复测试），批处理不新增回填路径；集成测试以真账本断言。
- [批结果被误用为 Task 进度] → 返回类型与 docstring 明示"仅代表这些动作的结局"；spec 场景锁定。

恢复保证沿用共享执行链：已确认完成的动作回填真实结果，未知受保护写动作停止待对账；只读动作在未确认状态的重试沿用既有分类契约。本 change 不宣称外部系统 exactly-once，不重写原执行器的序列化或只读策略。测试字典结果首跑字符串化、SQL 恢复 JSON 化时比较业务载荷语义与稳定身份，而非伪称文本字节一致。

## Migration Plan

无迁移。纯新增模块 + 测试；回退即 revert。

## Open Questions

（无——终态分类、恢复机制、竞争语义均来自 #277 既有实现。）
