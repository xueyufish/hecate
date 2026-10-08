# Why

Step6 仍有几类关闭门槛没有真正落地：受管命令和新 attempt 关联尚未形成完整闭环，平台与宿主的真实 HTTP/wheel 验收不足，内置执行 continuation 与冻结动作关联仍缺故障矩阵，工作流父子等待尚未接到真实节点或具名 adapter，租约与证据门还缺实际业务派发点的拒绝计数验收。这些能力不应继续拖到 Step7；只有企业审批、策略引擎、撤权语义和完整证据生命周期属于后续步骤。

## What Changes

- 按可实施顺序补齐 Step6 内部能力：受管命令持久投递、命令回执、新 attempt 平台关联、内置 continuation、工作流父子回调、动作边界保守授权、证据不可写/缓冲门、真实 HTTP/wheel 与 PostgreSQL 宿主故障验收。
- 明确 Step7 之前的保守边界：没有合法企业审批时只做技术 token/命令唤醒，不声明审批授权有效；没有中心策略时受保护动作 fail-closed，不做本地自授权。
- 把 Step6 完成定义为可运行验收结果，而不是组件测试通过：安装后的 Runner 进程、真实平台 HTTP、PostgreSQL durable store、进程 kill/restart、迟到结果和外部写回执丢失都必须有明确结果。
- 更新演进方案与 Step6 复核文档，将已完成、待完成和依赖 Step7/10 的事项分开。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `managed-runner-enrollment`: 受管命令下发、执行事实投影、平台 HTTP 验收和重连补传门槛。
- `standalone-runner-host`: 持久命令、等待唤醒、新 attempt、continuation、证据门和安装制品验收。
- `durable-execution`: 冻结动作关联、结果回放、未知写对账、PostgreSQL 故障矩阵。
- `platform-task-control`: 平台侧新 attempt 关联、工作流父子任务回调和真实命令效果。

## Impact

本 change 只完成 Step6 可闭环能力，不实现 Step7 的企业 policy engine、合法审批判定、职责分离、撤权策略或高风险离线窗口，也不实现 Step10 的完整证据生命周期治理。实施会触及 Runner CLI/HTTP、durable store、平台任务控制 API、共享执行服务、工作流 adapter、验收测试和演进文档。
