# Design

## Context

原生续跑的原材料已存在且经过验证:

- **内核**: `PregelRuntime.execute(session_id, ..., resume_value=...)` 走 `_restore_from_checkpoint`(checkpoint 缓存热路径 + 事件日志尾折叠;`packages/hecate-runtime/src/hecate_runtime/pregel.py`),恢复同一 engine 会话后从断点继续。
- **会话快照**: `WorkflowExecutionService` 在中断时已持久化会话快照(`_mark_session_interrupted` + checkpoint_store 的 `acquire_session_lock` + `save`),session 行翻为 `interrupted`。
- **动作安全**: durable 台账(`SqlActionLedgerHook`)按 action key 仲裁——已决回填真实结果、未决停止;这是 6c 交付的既有保证。
- **身份重验**: dispatcher 派发前的 Principal/部署/版本准入检查已存在(`test_queued_execution_revalidates_admitted_identity_and_version`)。

缺口在平台 durable 链路:`WorkflowExecutionService.execute` 无 resume 入参;`builtin.py` 观察把 interrupt 映射为 STOP_UNKNOWN("continuation controls are unsupported");dispatcher 的受保护中断分支一律转 `reconciliation_required`(`src/hecate/execution/task_dispatcher.py` "interrupted attempt contains protected actions")。

## Goals / Non-Goals

**Goals:**

- 内置后端的中断尝试在资格门通过时原生续跑:同一 Run、同一 engine 会话、已决动作回放、未决保守停止。
- 冻结执行定义摘要的记录与续跑校验(runner 同款模式落地到平台派发路径)。
- 故障矩阵:真实 dispatcher + SQL 存储 + 业务调用计数。

**Non-Goals:**

- 平台通用 checkpoint 引擎:checkpoint 所有权仍归内置执行后端(经共享装配注入的 checkpoint_store);dispatcher 只消费可装载性事实。
- 外部后端等效续跑:runner/托管后端按能力声明选择;本变更不改 runner 行为。
- G2 关闭:回执丢失仍保守停止进待对账;已知窗口不盲目重做的全局保证仍归 step6f/step16 矩阵。
- 聊天历史恢复:明确删除的路径不复活(`test_protected_interrupt_does_not_treat_conversation_as_checkpoint` 保持)。

## Decisions

1. **续跑标记经既有委托链穿透。** `WorkflowExecutionService.execute` 新增 `resume_interrupted: bool = False` 入参:为真时不把 messages 写为 initial_input,改为调用 Pregel `execute(resume_value=<唤醒/恢复载荷>)`,由内核 `_restore_from_checkpoint` 装载;`EntryExecutionService.execute` 的 `**execute_kwargs` 透传,无需新接口。备选(新建 resume 专用应用服务)引入第二条执行入口,拒绝。
2. **资格门在 dispatcher,快照可装载性是唯一新增 I/O。** 顺序:既 有准入重验(不动)→ 原 attempt 是否"已执行且中断"(projection/interrupt 事实,非 failed)→ 引擎会话快照存在且可装载(经 checkpoint_store 只读探测)→ 定义摘要一致 → 同 Run 续跑。任一不满足 → 维持 `reconciliation_required`(原因细分记录)。备选(在 workflow service 内部决定)会把续跑策略埋进会话层,dispatcher 失去对 attempt 生命周期的单一写入方地位,拒绝。
3. **定义摘要:派发时记录进 Run 快照,续跑时重算比对。** 摘要覆盖工具顺序与 Schema 引用、模型引用、persona/guardrail 引用(与 runner 的 `_host_definition` 同域,但字段取自 `Run.execution_snapshot`);首跑派发时计算并写入 execution_snapshot 的 `definition_digest` 键(既有 JSON 字段,无迁移);续跑前重算比对,不一致或缺失即拒绝。工作流服务执行期间的配置一律来自冻结快照(6 前置已保证),摘要防的是"快照之后的配置漂移被无声续跑"。
4. **中断事实如实记录。** `builtin.py` 观察遇 interrupt:维持 STOP_UNKNOWN(run 事实未决),detail 从 "continuation controls are unsupported" 改为携带 resumable 事实(会话已持久化、摘要值),供 dispatcher 资格门消费;不虚报成功,不假装已暂停。
5. **回执丢失 = 保守边界。** 续跑后台账仲裁遇到 claimed-undecided 的受保护动作:停止后续工具、Task 转 `reconciliation_required`。这与既有 `action outcome is not established` 分支同语义,6d 不改变它——原生续跑改善的是"可安全继续"的场景,"结果未知"仍归人工对账。
6. **验收走真实链路。** `tests/test_execution/` 的 worker-integration 同款 harness(真实 `PlatformTaskDispatcher` + SQL durable + 真实业务工具桩),不 monkeypatch `_execute`;故障注入用真实手段(新 dispatcher 实例接同一库模拟进程重启;账本行手工置 claimed-undecided 模拟回执丢失;篡改快照/摘要模拟漂移)。运行级 kill/restart 属 step6f 的 PG 宿主矩阵,本变更以"新 dispatcher 实例 + 同库"为进程边界等价物并如实标注。

## Risks / Trade-offs

- [resume_value 语义与 declarative interrupt 的交互(等待/唤醒路径已在 6c 落地)] → resume 只用于"中断后重驱动",等待路径继续走 TaskWaitingSignalError/`_park`;两类入口互斥并在测试中钉住。
- [checkpoint 缺失但事件日志完整时内核走 log-fold 冷恢复] → 资格门只认"快照存在且可装载",日志折叠恢复视为内核优化,不作为资格依据(避免把"历史可加载"当原生续跑证明——正是被删除的旧路径)。
- [摘要字段遗漏导致过度拒绝] → 摘要字段集在 design 冻结(工具顺序/Schema、模型、persona/guardrail 引用);实现时以负例测试钉住"改任一字段必须拒绝"。
- [同 Run 续跑与 revision/fencing 交互] → 续跑沿用既有 `expected_revision` 与 lease 校验路径,不新开写状态通道;并发注入测试保持。

## Migration Plan

无 schema/迁移(摘要入既有 execution_snapshot JSON)。回滚:resume 路径的资格门任一条件收紧即回到全量待对账;不删除已写入的摘要字段。

## Open Questions

(无——设计期已核对:Pregel `resume_value`/`_restore_from_checkpoint` 与 workflow 快照持久化均已存在;实施期待确认项仅为 workflow service 内部把 resume 标记接到 Pregel 调用的具体行级改动,属实现细节。)
