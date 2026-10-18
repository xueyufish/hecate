# 复核记录

结论：本 change 范围内发现的问题均已修正，相关回归及 PostgreSQL 迁移实测通过；可以进入提交与 PR 流程。当前保持未提交。

## 已修正问题

1. **[P1] 缺 workspace 被误当系统身份，历史越权**：原 list_publications 在 workspace_id=None 时放开过滤，违背 AuthContext 契约。现只有显式 is_system_scope 才允许跨租户；非系统缺 workspace 一律 404。
2. **[P1] 发布入口未校验归属，可替他租户生成发布凭据**：publish API 现向 service 传可信 AuthContext，门禁/审计/写凭据前校验 Agent 归属；保留受信内部调用兼容。
3. **[P1] 降级直接删掉不可重建的发布证据**：可变发布指针和通用审计无法重建普通发布报告。现有任何凭据（含软删除）就拒绝 downgrade；空表仍可降级/重升级。
4. **[P2] gate_config 来自判定后的可变 Agent 对象**：改为判定前 deepcopy；Run id/hash 同步冻结；报告 deepcopy 保存。判定期间配置改变或出现更新 Run 时仍锚原输入。
5. **[P2] 未参与回归的 baseline 被记录为已消费证据**：仅启用回归才查找、传入和记录 baseline。
6. **[P2] 四值封闭只在文档声明**：加入 ORM/迁移一致的 CHECK 与 Pydantic Literal 读验证，真实 SQLite/PG 均拒绝未知值。
7. **验证缺口**：原子性由构造器抛错改为真实 SQL 约束失败，覆盖 bypass 审计回滚；精确审计对账；历史 Agent 直接种子而非删除凭据；任务 JSON 真正持久化；补重复发布、只读不重算、分页边界和迁移护栏。

## 结果与保留边界

核心/护栏 29 passed；相关回归共 428 passed；PG drift 1 passed；共享 check all、strict OpenSpec、diff check 通过。详见 verification.md。

已 fetch 并对比 origin/main，新增的 step14 提交无文件交集。未 commit/push/archive。其他既有版本 CRUD 的统一 workspace 校验不在本切片，建议单独治理；本次发布与历史接口已封闭相应入口。
