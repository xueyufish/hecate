# 复核记录

结论：单回合组合顺序与跨阶段摘要绑定实现正确；发现的入口校验问题已修正，恢复与治理验证缺口已补齐。本 change 本地检查通过，保持未提交。

## 修正与补强

1. **[P2] 错误上下文或 ownership 类型在决策提交后才失败，或被当空上下文继续执行。** 原 AgentTurn 只检查 RunRef；空列表/False 被下游 truthiness fallback 吞掉，非空列表则提交后 TypeError，错误 owner 类型也到派发阶段才处理。现前置检查 dict/None 与非 None 的 DecisionOwnership 类型，错误输入在 lookup/模型/commit 前拒绝。None 不被改造成租约，真实 ownership/fencing 仍由原组件核验。
2. **真实结果与控制边界验证不足。** 补实际业务边界的提交顺序、fresh commit 摘要替换、取消/审批等待原信号透传、跨独立进程的完整原记录/真实载荷/稳定身份核验；补 A2 效果落地但回执丢失后的新进程安全停止、Task 状态不推进，以及 commit 后租约到期/真实 SQL 缺 owner 失败。
3. **本地 PostgreSQL 不可用的说明不准确，且无验证记录。** 已确认 55432 可达，按 locked postgres profile 同步声明驱动，独立临时数据库完成矩阵并清理，更新设计和新增 verification.md。

新增入口回归在原实现上 5 failed / 17 passed；完整相关回归 **410 passed、6 skipped**，双库矩阵 **132 passed、无跳过**；共享 ruff/format/mypy、strict OpenSpec 与 diff 检查通过。跳过项和覆盖交集见 verification.md。

生产修改保持在本 change 的两个 runtime 模块中；旧单动作/批处理测试及并行 step11/step14 文件未改动。未 commit/push/archive；CI 和 PR review 仍需后续完成。
