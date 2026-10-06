# Design

## Context

Iteration 2(#223)交付了受管执行闭环:接受→串行调度→执行→上传→投影。宿主 `LeaseGate` 已实现租约验证(签名/期限/aud 绑定/nonce 消费)并在每次 pull 后更新,但从未接入动作派发路径——`engine._dispatch_tool` 的保护动作只经过动作台账(gate_dispatch_async)、证据门与静态域检查(打点 domains)。租约 claims(iss/aud/sub/exp/nonce/tenant/delegation_ref)不含动作范围;enrollment 无 scope 字段。step6b 要求把受管授权接到实际 Action 意图/领取/工具派发入口,并按业务 API 调用计数验证断连到期、跨域与旧授权拒绝。

## Goals

- 受管保护动作的授权判定发生在动作边界(派发入口内,不可绕过),输入是当前租约而非配置快照。
- 授权拒绝显式、可计数、留证据;正常/拒绝两条路径都不破坏动作台账与对账语义。

## Non-Goals

- 审批绑定、策略引擎对接、撤权即时传播(超出租约 TTL 的陈旧窗口语义不变)——step7。
- 持久等待、checkpoint 恢复、工作流回调——迭代 4—6。
- 不引入新 Port/Protocol;`LeaseGate`、`ActionLedgerHook`、派发路径都是既有具体类型。

## Decisions

1. **租约门接在 `_dispatch_tool` 内、台账门之前(仅保护动作)。**RunState 增加 `managed: bool` 来源标记(由 `resume_managed` 置位;`_schedule_durable_resume` 的 standalone 路径不置位)。保护动作(effect ≠ readonly)先 `gate.check()`:拒绝 → 返回 `{"status": "authorization", detail}`(与域拒绝同形),业务 API 不调用,证据记 denial;readonly 不经租约门(与方案"低风险离线行为"一致,且保持既有派发语义)。放在台账门之前的原因:无授权时不应产生新的动作意图/领取——台账记录"系统尝试过"会把拒绝伪造成执行事实。
2. **租约 scope 是数据域允许列表,由平台签发。**`Claims` 增加可选 `scope: list[str]`;`lease_claims` 签发时写入、HMAC 摘要自动覆盖;`verify_lease` 原样返回。平台侧:`StandaloneEnrollmentModel` 增加 `managed_scope` JSON 列(Alembic 迁移,默认空),`SetManagedOptIn` 扩展接受 scope(操作员受控,网络事件不可变更——复用既有 admission 语义);`issue_lease_for` 按 enrollment 签发。宿主验证:受管保护动作的请求域必须 ∈ lease.scope(lease 无 scope 或空 scope = 默认拒绝);打点 domains 仍是派发参数之外的域检查基础,两者取交集判定。
3. **nonce 消费即"旧授权"判定。**`LeaseGate.check()` 已拒绝已消费 nonce(重放)——每次保护动作消费一次 nonce 意味着每份租约只授权一个保护动作,这是刻意的:pull 频率决定授权节奏,陈旧窗口就是 lease_ttl。同一租约内多个保护动作由下一轮 pull 的新租约覆盖。
4. **重启语义 = 空 gate 拒绝。**重启后 `LeaseGate` 无租约(内存态),首个保护动作被拒绝(留证据),通道循环重连后 pull 附带新租约,后续动作恢复——与"断连到期拒绝"同一代码路径,不新增恢复特例。
5. **拒绝结果与运行终态。**授权拒绝是工具结果(outcome),不是运行失败:受管运行继续执行图内后续节点并按既有规则收敛终态;保护动作的业务副作用计数为零。结果未知的动作仍走台账对账——重新授权只影响"新动作",不重做"已领取未决"动作。

## Risks

- 每保护动作一个 nonce 使高频写场景的吞吐受 pull 间隔限制:这是限域限时授权的既定语义(方案明确要求当前在线判定或限时授权),不做批量 nonce。
- scope 双源(租约 scope × 打点 domains)在配置漂移时可能过度拒绝:拒绝显式且可查询,操作员修正 enrollment scope 或宿主 data_domains 后恢复,不做静默放行。
