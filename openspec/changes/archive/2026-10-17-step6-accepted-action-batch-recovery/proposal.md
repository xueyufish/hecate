# Proposal

## Why

#277 交付了单个已接受动作的安全执行与恢复（`AcceptedActionStep.dispatch`：账本回填、fencing、稳定 execution id、零模型调用），但一个已提交决策通常携带**有序的多个动作**。演进方案验收矩阵"同一决策的部分动作完成——新进程识别已完成、未开始及结果未知的动作，复用真实结果；未知副作用先对账，不能整批重试"的行因此只有单动作证据。本 change 新增一个具体组合件：加载已提交决策，按持久化顺序串行调用现有 `AcceptedActionStep`，验证"同一决策部分动作完成后继续恢复"。

## What Changes

1. **新增 `accepted_action_batch.py`**（hecate-runtime）：批处理入口接收 `run_ref` 与 `decision_step_id`，从 `AcceptedDecisionStore` 加载的持久记录派生动作与顺序——不接受调用方替换、增删或跳过动作；逐动作按序调用既有 `AcceptedActionStep.dispatch`，携带首次加载记录的 content_digest 逐次核验，禁止装配不同存储或中途返回另一份有效记录而替换原动作事实。
2. **恢复即重新派发**：已完成动作由账本按稳定 execution id 回填真实结果（`succeeded` 携带原结果）；恢复时从原动作列表重新核对，不依赖任何内存游标。任何动作返回 `failed`、`withheld` 或 `stopped` 即停止本次处理，后续动作零派发，返回值携带逐动作结果与未处理动作键；停止原因区分决策内容冲突、存储不可用/损坏/记录消失、ownership 失效和参数校验拒绝，不统一伪装成授权拒绝。取消和持久审批等待信号保持透传。
3. **逐动作沿用全部治理**：ownership、fencing、授权、审批与稳定 execution ID 由每个动作的 `dispatch` 自行校验（批处理不做整组前置检查后放行）——A1 完成后租约过期或被接管时 A2 被拒绝。
4. **诚实语义**：批结果仅代表这些动作的结局；不推进 Task 成功、不触发重规划、不并发派发；跨进程竞争时同一写动作由账本 fencing 保证只有一个首次执行者。

**边界**：限定一个决策内的有限串行动作集；不扩展多轮 Controller、并行动作调度、自动重规划、入口切流；无新表、无迁移。

## Capabilities

### New Capabilities

（无。）

### Modified Capabilities

- `accepted-agent-decision`: ADDED「同一已接受决策的串行批处理与恢复」——按持久顺序串行派发、逐动作治理校验、终态即停且后续零派发、恢复经账本回填且不盲目重做、逐动作结果与未处理键返回。

## Impact

- **代码**：新增 `packages/hecate-runtime/src/hecate_runtime/accepted_action_batch.py`；`accepted_action_step.py` 仅在必要时小幅提炼公共校验复用（原单动作调用语义保持兼容；新增可选 expected_decision_digest 供批处理约束原决策）。
- **测试**：runtime 单测（批编排、终态停止、未处理键）+ durable 集成测试（跨真进程恢复、A1 落盘后进程退出、租约过期/接管、双进程竞争、SQLite 与 PostgreSQL 双库）。
- **规范**：`openspec/specs/accepted-agent-decision/spec.md`（经本 change delta 归档时同步）。
- **不涉及**：Task 成功推进、Controller 循环、并行动作调度、重规划、入口切流、新表/迁移；与 `step11-agent-publication-history-ui`（前端域）无交集。
