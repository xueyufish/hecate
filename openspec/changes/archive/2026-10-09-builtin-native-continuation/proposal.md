# Proposal

## Why

方案 step6d 的关闭门槛(经 `step6-status-correction` 更正)要求"平台共享执行真实 continuation、冻结动作关联和外部写/回执丢失故障矩阵"。现状:`task_dispatcher.py` 对含受保护动作的中断尝试一律转 `reconciliation_required` 并以新 attempt 重放,`builtin.py` 在中断时标记 "continuation controls are unsupported"。但原生续跑的原材料已在:Pregel 内核 `execute(resume_value=...)` 走 checkpoint 缓存 + 事件日志折叠恢复同一 engine 会话;`WorkflowExecutionService` 中断时已持久化会话快照;动作台账已具备已决回填/未决停止仲裁。缺的是平台 durable 链路的续跑接线与资格门——dispatcher 从不为中断尝试走原生续跑路径。

## What Changes

- **续跑标记穿透**:dispatcher 判定可续跑后,经 `EntryExecutionService` → `WorkflowExecutionService.execute` → Pregel `resume_value` 原生续跑同一 engine 会话;`WorkflowExecutionService.execute` 新增 resume 入参(不写 initial_input,由内核从 checkpoint+日志恢复);`builtin.py` 的中断观察从 "unsupported" 改为如实的 resumable 事实记录。
- **续跑资格门**(全部满足才续跑,否则保持保守待对账):原 attempt 确实中断(非失败);引擎会话的持久快照/checkpoint 存在且可装载;冻结执行定义摘要一致(实施时记录派发摘要——工具顺序/Schema、模型、persona/guardrail 引用——续跑前重算比对,漂移拒绝);身份/部署/版本重验(既有准入检查保持在前)。
- **同 Run 续跑**:可续跑时沿用原 Run,不铸造新 attempt;动作台账仲裁保证已决动作回填真实结果、未决动作停止。外部写成功但回执丢失 → 续跑停在下一动作前,Task 转 `reconciliation_required`(G2 保持未关闭)。
- **故障矩阵验收**:真实平台 dispatcher + SQL durable 存储(worker-integration 同款 harness,不 monkeypatch `_execute`),用业务 API 调用计数覆盖:中断后重启续跑零重做、回执丢失保守停止、定义摘要漂移拒绝、身份漂移顺序断言。
- 不建设平台通用 checkpoint 引擎:checkpoint 所有权仍归内置执行后端,平台只保存引用与恢复状态;不承诺外部后端等效续跑(它们按能力声明选择原生恢复或待对账)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-task-control`: 修改"平台中断尝试优先恢复原执行会话"需求——补冻结执行定义摘要前提与显式场景(摘要漂移拒绝续跑、回执丢失保守停止、续跑沿用原 Run 的台账零重复),使需求与本次接通的实现语义一致。

## Impact

- 产品代码:`src/hecate/studio/workflows/execution_service.py`(execute 新增 resume 入参)、`src/hecate/execution/entry_service.py`(透传)、`src/hecate/execution/task_dispatcher.py`(资格门 + 同 Run 续跑 + 摘要记录)、`src/hecate/execution/builtin.py`(中断事实记录)。
- 测试:`tests/test_execution/`(workflow 服务级 resume 单测 + 真实 dispatcher 集成故障矩阵)。
- 文档:`docs/refactor/enterprise-agent-platform-evolution-plan.md`(step6d 条目)、`docs/refactor/step6-followup-review.md`(验收记录)。
- 不涉及 step7 授权语义;不改变 runner 侧行为;无 schema/迁移变更预期(摘要落 Run 快照既有 JSON 字段)。
