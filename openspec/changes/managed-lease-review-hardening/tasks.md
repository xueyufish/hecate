# Tasks

## 1. 租约边界

- [x] 1.1 修复平台窗口签发、可信期限、缺失/无效 iat 和非有限配置，以租约门和 HTTP/签发负例验证。
- [x] 1.2 接通实际治理链的审批补位、scope、撤销与独立来源隔离，以真实 ledger/gate/profile/派发组合测试验证。

## 2. 通道配置与状态

- [x] 2.1 完整计算本地指纹并正确维持漂移/已知失效状态，以配置变更及通道 HTTP 回归验证。

## 3. 平台事件

- [x] 3.1 修复 UUID/跨方言迁移并补无 workspace 唯一索引，以 SQLite 与可用 PostgreSQL 迁移回归验证。
- [x] 3.2 确保真实竞争收敛及 event_id 异体拒绝，以强制首次碰撞、原事务仍可用和投影回归验证。

## 4. 复核交付

- [x] 4.1 核对 #251/#252 规格归档，修正文档认证表述，完成 review.md；执行 scoped pytest、ruff、mypy 和 OpenSpec strict validate 并记录实际结果。
