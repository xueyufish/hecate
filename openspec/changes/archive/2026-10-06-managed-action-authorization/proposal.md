# Proposal

## Why

演进方案 step6b(受管动作时授权)未接通:宿主 `LeaseGate` 只是库级验证器(单测覆盖),从未进入实际的动作意图/领取/工具派发入口——受管运行的保护动作派发只经过动作台账与静态域检查,不验证当前租约;租约也不携带动作范围,平台下发的限域限时授权与实际派发脱节。结果:断连超过租约期限后,受管保护动作仍可能执行;旧租约(已消费 nonce)、跨域请求与实际业务副作用的计数验证缺失。这与方案"断连不能自签权限、授权到期禁止新动作"的受管语义不符。

本 change 是迭代 3 的落地切片:把受管授权从"验证器存在"推进到"动作边界强制执行"。

## What Changes

- **租约携带动作范围**:claims 增加 `scope`(数据域允许列表);平台按 enrollment 的操作员受管范围(`managed_scope` 列,经既有 operator opt-in 面 SetManagedOptIn 扩展)在每次 pull 时签发限域限时租约;签名摘要自动覆盖 scope。
- **动作边界强制验证**:受管运行的保护动作(非 readonly)在动作意图/领取之前经 `LeaseGate.check()` 验证当前租约——签名、期限、部署绑定(aud)、未消费 nonce,且请求域必须被租约 scope 允许;拒绝产生带计数的显式拒绝结果(status `authorization`),业务 API 不被调用,拒绝留证据;readonly 派发与恢复路径的 ledger 语义不变。
- **恢复与重连语义**:重启后租约门为空——保护动作在首次成功 pull 前被拒绝;重连取得新租约后新保护动作恢复执行;结果未知的动作仍只能对账(ledger 仲裁不变),不因重新授权重做。
- **身份一致性**:租约主体/部署绑定与接受时身份打点同源校验(同一 trust_root 派生),跨宿主/跨部署租约不能驱动本宿主动作。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `managed-runner-enrollment`:受管租约从"验证器存在"升级为"动作边界强制"——保护动作派发前验证当前租约与动作范围;断连到期、旧授权、跨域拒绝以实际业务 API 调用计数验证。
- `standalone-runner-host`:受管运行的保护动作派发入口接入租约门;受管身份打点与租约来源一致性校验。

## Impact

- `packages/hecate-durable`:`Claims` 增加 `scope` 字段;`lease_claims` 签发、`verify_lease` 返回 scope(摘要自动覆盖)。
- `src/hecate`:`StandaloneEnrollmentModel` 增加 `managed_scope` 列(Alembic 迁移);operator opt-in API 接受并校验 scope;`issue_lease_for` 按 enrollment 签发带 scope 租约。
- `packages/hecate-runner`:`engine._dispatch_tool` 在保护动作前接入 `LeaseGate.check()` 与 scope 检查(受管运行标记经 `resume_managed`/接受路径传递);拒绝结果与证据记录。
- 测试:动作边界验证、断连到期、旧授权重放、跨域拒绝、重连恢复——全部以业务 API 调用计数断言;既有 lease/通道/调度回归。
- 不扩展:审批绑定、策略引擎对接、租约之外的撤权传播(均为 step7);等待/恢复、checkpoint、工作流验收(迭代 4—6)。
