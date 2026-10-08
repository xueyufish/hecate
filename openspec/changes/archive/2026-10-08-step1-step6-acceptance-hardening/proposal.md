# Proposal

## Why

最新 main 已合入 Step6a～Step6f，但等待、恢复、命令重放和回调的组合路径仍可能绕过既有身份或副作用保证。需要以真实执行边界的负例补齐验收，明确组件交付与完整部署认证的差别。

## What Changes

- 修复 Runner 唤醒的并发消费、请求绑定、内部字段注入与前序动作重复写；checkpoint 按 attempt 隔离，消除唤醒提交与清理间的崩溃窗口。
- 修复命令重放的 workspace、task、issuer、内容绑定，回调核实完整子引用并保护可信子任务事实。
- 平台含受保护动作的恢复仅允许真实原生续跑；普通聊天历史不能充当 checkpoint，当前未接通续跑时保守对账。
- 修复 checkpoint 跨会话读取、受管多来源事件补传阻塞，以及受管授权与副作用未知时的停止语义。
- 重验执行时身份/冻结配置、Runner 接收时定义摘要与 Lease 的宿主/工作区绑定；隔离 Task 读取，并收紧无恢复版本证明的旧任务迁移。
- 补真实 wheel 审批等待重启验收，修正 SC03 重启后 fixture 子进程泄漏，保留 wheel 构建失败诊断。
- 回归 Step1～Step6 相关契约、分层和安装测试，新增审查报告并更新规划的完成证据和未完成项。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `standalone-runner-host`: 唤醒保留动作身份，内部状态只能由宿主生成，checkpoint 隔离与未知结果停止；冻结执行定义、Task owner 读取与生产 Lease 身份绑定。
- `platform-task-control`: 命令和回调重放必须绑定授权资源及原请求；普通会话历史不足以授权原生恢复；执行时重验身份与冻结配置。
- `durable-execution`: checkpoint 指定读取必须属于请求会话。

## Impact

影响 Runner、durable 存储、平台 Task 控制与派发、相关回归测试和 refactor 文档；不引入供应商绑定，不修改 main，不推送，不承诺尚未验收的受管/PostgreSQL 进程组合。
