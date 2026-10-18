# 复核记录

结论：本 change 范围内发现的问题已修正，相关本地验证通过；保持未提交，可进入提交和 PR 流程。

## 已修正问题

1. **[P1] 批处理与单动作可消费不同决策。** 构造器分别注入 step/store，原实现只校验两份记录各自有效，无法保证内容一致；同一 Run/step 的另一份有效记录可以替换参数或顺序。现 batch 固定初次 content_digest，逐动作通过可选 expected_decision_digest 在 executor 前核验，冲突停发。既有单动作调用默认不传参数，旧 26 项测试零改动通过。
2. **[P2] 存储故障被吞并为授权拒绝，记录消失还会丢失报告前缀。** 现明确 decision_conflict / decision_store_unavailable / stale_ownership / invalid_action，保留已处理前缀与未处理动作；底层 needs_review/reconciliation_required 不改写，取消/审批等待透传。
3. **[P2] 批入口在存储访问后才验证 step 身份。** 现共享 helper 在 lookup 前拒绝空白、过长、非字符串等标识；非法身份不得触及存储。
4. **[P2] 独立双进程竞争任务勾选但没有实现。** 原用例只有同进程 asyncio 并发。现增真实重叠的两个 -I 解释器、写前后屏障、第三进程全结果恢复，SQLite/PostgreSQL 均通过；补合法换主后恢复继续，而非仅验证旧 owner 被拒。

新增回归在原实现上 10 failed / 9 passed。最终完整相关回归 **379 passed、6 skipped**；SQL 双库矩阵 **116 passed、无跳过**；共享 ruff/format/mypy、strict OpenSpec 与 diff 检查通过。具体命令、跳过项和重复覆盖说明见 verification.md。

未提交/推送/归档，未改并行发布历史 UI worktree。保留共享执行链的只读重试和结果序列化语义；本切片不替代完整 Controller 或外部 exactly-once 认证。
