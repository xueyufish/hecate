# Why

Step6 在已交付查询面仍存在证据分页和尝试状态不一致：正式事件页总是声明无后续页，旧 Run 在宿主重启后继承 Task 当前状态，等待和排队事件流被误报为终态。需要补真实 HTTP 回归并修正，保留尚未关闭的 Step6 门槛。

## What Changes

- 持久事件页提供真实后续页标记，包含缺口占用页容量的情况。
- 旧 Run 从自己的持久状态事件恢复视图，不继承后续尝试的状态和等待 token。
- 排队、等待和待对账不作为最终完成；完整事件页读取后才关闭兼容事件流。
- 重启后返回原提交时间/幂等键/错误/产物引用；重复取消共用未决命令，不覆盖其回执，已完成的持久 Run 返回拒绝而不是内部错误。
- 复核已交付 Step6 代码，更新主线交付事实和剩余验收项。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `durable-execution`: 事件页精确声明后续内容；提供按 Run 查询最近状态事件。
- `standalone-runner-host`: 状态、等待、事件分页在重启和新尝试后保持一致。

## Impact

修改 durable SQL 读侧、Runner 契约/HTTP 查询、回归测试和复核文档；无 schema migration，不实现尚未规划完成的企业审批或平台 continuation，不改变独立组件边界。
