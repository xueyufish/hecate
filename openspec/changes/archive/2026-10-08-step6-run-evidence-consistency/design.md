# Design

## Context

Task 状态由当前执行尝试推进，Run 是固定的一次尝试。旧 Run 的查询必须使用其自己的事件流；事件超过分页上限时不能用错误的 has_more 截断证据。

## Decisions

1. SQL EventPage 添加默认 false 的 has_more，SQL 多读一条，并以页实际推进的 cursor 判定是否还有已观察事件。缺口标记同样占页容量。
2. SqlEventLog 提供按 Run/source 和事件类型检索最近事件的方法。DurableRuntime 对当前 Run 使用 Task 状态，对历史 Run 仅使用其自身 task_state/run_terminal 事件；缺失证据为 unknown。
3. 只有当前未消费等待对应的 Run 可返回等待 token。已完成旧 Run 保留其原有错误事实。
4. 兼容事件流仅在最终成功、失败、取消且本页已耗尽时设置 terminal。等待/待对账仍保留为非最终状态。
5. 不引入新执行生命周期、通用 checkpoint 或供应商依赖。
6. 提交回执时间从 SubmissionRow 恢复，幂等键从原关联恢复；成功的持久 Run 恢复已有事件产物引用，失败保留原错误。
7. 在执行 loop 内不让出控制地选择并登记取消命令，运行中重复取消复用同一未决命令。完成后的新取消使用新拒绝回执，不覆盖已应用回执；无内存执行的历史 Run 不抛 KeyError。
8. 本地运行取消的终态、applied 命令回执及事件通过已有 apply_task_state 事务共同提交，移除终态写后再更新 applied 的崩溃窗口。状态未知/待对账时保留 requested，不伪造已拒绝或已应用效果。

## Risks / Validation

EventPage 新字段有默认值，已有构造调用兼容。SQLite 验证真实 HTTP、重启与缺口分页；SQLAlchemy JSON 条件保持可移植。回归完整 Runner/durable、平台受管投影、契约、mypy 与分层检查；未跑 PostgreSQL 时明确登记。
