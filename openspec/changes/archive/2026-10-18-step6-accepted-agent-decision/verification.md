# 验证记录 — step6-accepted-agent-decision

复核后的实际结果见 [review.md](review.md)。初稿虽通过原有测试，但其 token 比较不验证权威租约、未写 outbox、同进程新实例不构成跨进程证据，故原“核心验收已通过”结论已被本记录替代。

## 最终检查

- 共享检查：ruff check/format、mypy 全部通过（1585 个格式文件、940 个源文件）。
- 显式收集 `packages/hecate-runtime/tests/`、`packages/hecate-durable/tests/`，并运行 Runtime 自足、kernel purity、domain layering、durable contract 与 drift pins：**197 passed, 6 skipped**。
- 专用 PG 的新决策共同契约与并发/进程恢复/迁移专项：**74 passed**。
- 专用 PG 的 durable 全套双数据库回归：**153 passed, 1 skipped**。SQLite 不能证明的既有并发用例只跳过 SQLite 分支，PG 分支已运行。
- Runtime 新组件与 PG 专项定向复跑：**16 passed**。
- OpenSpec 严格校验 valid，diff whitespace 校验通过。

## 关键证明

- 首次接受和幂等重投均校验 owning Task 的现有 dispatch 租约、holder、精确 generation 与期限；错误 owner 不因步骤无旧记录而获准。
- 决策与 outbox 同事务；故障注入验证两个事实同时回滚；重投不重复事件。
- 嵌套对象修改、输入版本变化和内容损坏均不会静默改变/复用原决策；无存储异常重问模型路径。
- 两个真实 Python 子进程共享 PG，写进程提交后退出，读进程模型零调用并找回原内容。
- 在隔离 PG schema 验证本 revision 的 metadata 一致性和保留已插入记录的回退拒绝。应用回退保留新表，不执行删除决策事实的 downgrade。

## 限制

仅单个带动作决策步骤；无完整 Controller、工具派发、真实模型质量认证或入口迁移。本次没有提交、推送、主 spec 同步或归档，未把本地验证等同 CI/生产认证。
