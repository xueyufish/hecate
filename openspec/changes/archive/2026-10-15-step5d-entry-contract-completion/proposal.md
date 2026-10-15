# Proposal

## Why

演进方案 step5d 第一条剩余项仍留四个确定性缺口([step5d 修正复核](../../../docs/refactor/enterprise-agent-platform-evolution-plan.md) 及 [复核记录](../../archive/2026-10-13-step5d-review-hardening/review.md)):同步执行失败时事件集合未返回、停止错误未形成完整终态信封;调用方事务回滚会把 Task/Run 登记与投影一并丢弃;结果中声明的产物引用未映射进公开契约;聊天执行信封的持久化仅在 SQLite/内存组合上验证,未在 PostgreSQL 上经实际入口端到端认证。step5e 真实模型验收依赖这些状态与错误语义,应先收口。

## What Changes

- 同步与流式失败均形成完整终态信封:尽力映射已排空的引擎事件,终态信封携带 terminal state、停止类别与安全的错误详情,写入事件存储并可按 run 游标分页读取;失败不伪报成功。
- 登记与失败事实在调用方外层事务回滚后仍可查询:显式划分登记、终态投影与事件各自的事务所有权;修复 MUST NOT 提交调用方业务事务。
- 声明产物引用完成公开契约映射:结果声明的 output artifact refs 经 workspace 归属校验后进入 Run 投影与终态信封;跨 workspace 或未声明引用显式拒绝,不静默丢弃。
- 以上行为经 PostgreSQL 实际入口端到端回归:沿用 `HECATE_STEP6_POSTGRES_URL` 双引擎参数化,经实际 HTTP 入口执行后以新数据库会话查询并排除内存兜底,证明信封真正来自持久存储;未配置 URL 时显式 skip。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `platform-entry-execution`: 新增失败终态信封、登记事务所有权、产物引用映射与 PostgreSQL 端到端回归四组要求。

## Impact

- 代码:`execution/entry_service.py`(失败路径事件映射、终态信封、事务所有权)、`execution/entry_events.py`(终态信封类型)、`execution/task_run_registry.py`(投影字段扩展)、HTTP mapper 渲染;不新增 Runtime 扩展点。
- 测试:入口回归(SQLite)+ PostgreSQL 参数化端到端;ASGI 测试即可,真实 TCP 中途断连仍归 G3,不混淆。
- 不变更:默认引擎路由维持现状;独立 workflow 的 Run/部署身份、G3 真实断连证据、A2A per-agent 映射均不在本 change。
- 收尾:四项验收通过后关闭演进方案 step5d 下第一条剩余项(同步失败信封、登记保留、产物映射、持久后端入口认证四子项),不是关闭整个 step5d。
