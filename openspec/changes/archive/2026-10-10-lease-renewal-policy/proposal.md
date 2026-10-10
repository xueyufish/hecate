# Proposal

## Why

Step6b 的技术闭环尚未完成:受管租约当前是"一次受保护派发"的一次性授权,同 Run 多受保护动作依赖引擎内 5 秒硬编码的有界等待等通道拉新租约,策略参数不可声明、续租语义没有规格与进程级验收;且 `LeaseGate` 的 nonce 消费记录是进程内存,Runner 重启后丢失,重启窗口内回注未过期旧租约可再次武装动作(重放窗口)。演进方案 step6b 的技术部分(多写动作续租或拒绝策略 + 失联/过期/nonce 重放零副作用验收)需要独立交付,与 step7 的工具级动作范围、合法审批判定解耦。

## What Changes

- 续租策略正式化:同 Run 第二个及后续受保护动作在租约已消费时进入**有界等待续租**(等待通道下一次 pull 安装新租约;等待预算成为显式声明的策略参数,默认沿用现值 5 秒),超时转显式证据化拒绝(执行失败、停止后续工具、零业务副作用)。等待仅覆盖"新租约未到达"这一瞬态;范围越界与身份不匹配**立即拒绝**,不等待。
- 重放窗口收口:`LeaseGate.update` 在安装前先验证签名/期限/部署绑定;nonce 消费记录持久化到宿主本地状态(重启后仍拒绝已消费租约的回注);平台 pull 的无状态重发与宿主本地旧租约回注都不得再次武装动作。
- 失联窗口声明化:通道停止 pull 后,宿主在声明的续租预算内可完成在途多写动作链,超窗后新受保护动作停止;窗口值进入宿主 profile/文档,不再只是源码常量。
- 进程级验收套件(SC05 同族,wheel + 真实平台 HTTP):多写动作续租成功链(每个动作恰好一次授权、业务调用计数逐一核对)、断连/停发租约后有界拒绝、租约过期拒绝、nonce 重放(含跨重启回注)零副作用、已决动作回放跳过闸门的回归保持。
- 明确不做:工具级动作范围、合法审批判定、撤权流程归 step7;平台侧事件日志追加的 fencing 不在本 change(平台 EventStore 与租约的接线是另一条信任边界,归 step6f 矩阵与 step7 撤权窗口)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `managed-runner-enrollment`:扩展"限时授权租约约束受管动作且断连不自授权"需求——新增同 Run 多受保护动作的有界等待续租语义、续租预算/断连窗口声明、重启后 nonce 重放窗口收口,并补对应验收场景。

## Impact

- `packages/hecate-runner/src/hecate_runner/managed.py`:`LeaseGate.update` 安装前验证、nonce 消费持久化(宿主本地状态文件,复用 evidence/状态目录)。
- `packages/hecate-runner/src/hecate_runner/engine.py`:`_lease_refusal` 的等待预算改为可声明参数(profile/装配注入),拒绝语义保持。
- `packages/hecate-runner/src/hecate_runner/profile.py` / `server.py`:续租预算参数的声明与文档。
- `tests/scenarios/`:新增 6b 进程级验收套件(复用 `tools/managed_platform.py` 平台栈),场景清单补 slices。
- 无新表、无 migration;租约格式与 HTTP 契约不变(pull 响应仍携带新租约)。
