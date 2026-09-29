# Design

## Context

见 proposal.md。执行标识继续沿用 session/tool_call_id，记录增加工具身份校验而不重分配 key；不实现 Step6/7 的完整 Action 模型。

## Goals / Non-Goals

保证 Step1 安全停止与角色权限；不承诺跨供应商 exactly-once 或新增自动对账。

## Decisions

- 使用 `session.begin()` 内的 `pg_advisory_xact_lock`，事务退出释放数据库锁，避免依赖连接池关闭物理连接。通过事务内 `lock_timeout` 约束等待，不让该设置污染连接池。
- 保留参数摘要格式，单独校验记录的 `tool_name`；遗留 claim 有参数时计算摘要。缺失身份的已存在记录安全停止。严格拒绝非 object、非法 JSON 和非 JSON 可序列化参数，不降级到空参数。
- 最新 TOOL_CALL 清除前次 receipt 的状态。失败后重试在领取锁内追加 TOOL_CALL；已有 claimed 的写操作一律停止。只读可重复执行，写操作不以静态名称幂等分类推断尚在执行的调用已退出。
- 非法 receipt 状态视为 outcome_unknown。成功结果恢复保留通道消息的 error 标记；不在此阶段改造结果持久化。

- 真实 PG 测试确认 MAX(version) FOR UPDATE 非法。append/append_batch 使用独立双整数 advisory lock 命名空间，先锁再查询 MAX 并写入；与外层单整数领取锁隔离，避免两条连接自锁。批量写入冲突检查在 commit 前完成，避免部分提交。

## Risks / Trade-offs

- 历史记录没有工具身份或参数 → 安全停止，不能猜测。
- SQL/mock 测试不能验证连接池锁释放 → 提供 opt-in 真实 PostgreSQL 回归，执行情况明确登记。
- 自动重跑幂等写收紧 → 人工核对未决调用；失败终态仍遵循已定义的幂等重试规则。

## Migration Plan

无需迁移表结构；保留旧事件读取。按定向测试验证后合入，不能回退恢复盲目写入。
