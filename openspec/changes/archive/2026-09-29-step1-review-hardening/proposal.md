# Proposal

## Why

Step1 review 发现 MCP 文件写入仍未检查 editor 角色，Postgres 领取锁会残留在连接池，恢复记录只校验参数而没有校验工具身份。现有测试未覆盖这些反例，G1/G2 的关闭证据需要补齐。

## What Changes

- MCP `write_file` 在创建执行器前检查 editor 权限。
- Postgres 领取锁改用事务级锁并落实等待超时，正常、异常和取消退出均释放锁。
- 恢复校验工具名及参数，拒绝非法参数；遗留记录利用已保存参数校验，不把非法状态当作普通失败。
- 未决写领取不自动重跑；允许重试的失败执行在锁内重新领取，状态按最新领取/结果顺序解释。
- 更新基线，明确内存契约、SQL 单测和真实 Postgres 验证的区别。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `mcp-server`: 直接文件写入遵守角色授权。
- `tool-recovery`: 工具身份、有效参数、未决写领取及重试领取语义。
- `eventstore`: 领取锁的事务生命周期及等待超时，纠正仍声称生产实现 no-op 的旧规范。

## Impact

涉及 MCP server、ToolWorker、PostgresEventStore、相关测试与 Step1 基线。无需数据库迁移或新增依赖；已领取但未决的幂等写将安全停止，需人工核对。
