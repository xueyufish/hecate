# Proposal

## Why

复核 #251–#253 后，#253 的组件测试未覆盖真实装配组合：本地审批桥会被受管 profile 再次拒绝，平台陈旧窗口未执法，配置指纹遗漏授权配置，事件迁移与竞争去重仍有边界缺陷。修正这些确定性问题，使已声明契约在实际调用链成立。

## What Changes

- 用完整治理与审批权威验证断连审批桥，保持 scope、撤销、硬拒绝及一次性消费；独立运行沿用本地 profile。
- 租约期限统一经可信时间；配置了陈旧窗口却缺少有效 iat 时拒绝；平台窗口通过签名 exp 收紧；拒绝非有限窗口。
- 指纹覆盖本地授权与受管配置；漂移标记只在受门禁校验的成功调用后清除；已观察撤销进入安全底线。
- 修复 PostgreSQL 历史重复位置修复 SQL、UUID 最早行规则、无 workspace 位置唯一性及竞争 event_id 异体拒绝。
- 补充真实派发、窗口、指纹、存储竞争回归，记录认证边界。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `managed-runner-enrollment`: 平台窗口与完整本地审批补位执法。
- `standalone-runner-host`: 组合装配、漂移与撤销的正确性。
- `platform-task-control`: UUID/空 workspace 位置修复和异体去重。

## Impact

涉及 governance、durable 契约消费、runner 装配与派发、managed API、平台事件服务和迁移；不增加依赖或新 Protocol，不启用受保护写入，不承诺完成 step16 全认证。保留缺省 lease_policy 的兼容语义，显式声明的版本与窗口严格执法。
