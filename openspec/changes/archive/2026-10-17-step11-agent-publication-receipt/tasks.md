# Tasks

## 1. 模型与迁移

- [x] 1.1 `src/hecate/models/agent_version.py`：新增 `AgentPublicationModel`（表 `agent_publications`；字段与索引见 design D1；docstring 说明只追加、Run 身份锚定、outcome 四值与 NULL 语义）与 `AgentPublicationReadSchema`（字段白名单：version、agent_config_hash、candidate/baseline Run id 与 hash、gate_config、gate_report、outcome、operator_user_id、created_at）。验证：mypy 通过；schema 往返。
- [x] 1.2 新增迁移 `019_agent_publication_receipt`（建表 + `(agent_id, created_at)` 与 `(workspace_id, agent_id)` 索引，锚 `018_evaluation_scope`，无回填，空表可 downgrade、有凭据则拒绝降级）。验证：`alembic heads` 单一；本地 PG 可达时实跑 `tests/test_migrations/test_upgrade_drift.py`。

## 2. 发布路径写入凭据

- [x] 2.1 `AgentVersioningService.publish_version`：按 D2 顺序写入 publication（bypass 审计后、指针移动前，同 flush）；outcome 四值推导（off/force_bypassed/warn_passed/passed）；Run 身份取判定时已持有的对象（含 baseline 为 None 的 NULL）；`gate_off` 时 gate_report 存最小快照而非"通过"语义。验证：单测覆盖四 outcome 推导与 Run 哈希锚定。
- [x] 2.2 原子性与拒绝路径：凭据 flush 失败（约束注入）→ 发布整体失败、指针不动、无残留记录；require 拒绝 → 无任何记录。验证：单测注入失败并断言指针与记录双双不变。

## 3. 历史查询

- [x] 3.1 `AgentVersioningService.list_publications`：workspace 归属校验（跨 workspace 表现 not found）、`created_at desc, id desc` 排序、limit/offset 分页（limit ≤100 默认 20）。验证：单测覆盖排序/分页/跨 workspace 404 语义。
- [x] 3.2 `src/hecate/studio/api/agent_versions.py`：`GET /api/agents/{agent_id}/publications`（AuthContext 照旧，返回白名单 schema）。验证：API 测试覆盖 200 分页结构与 404。

## 4. 回归与验证

- [x] 4.1 主断言回归（新独立测试文件 `tests/test_api/test_agent_publication_receipt.py`）：发布后改 gate 配置 + 改任务 + 补跑新 Run → 旧记录 outcome/Run ID/报告快照逐字节不变；读取不触发门禁重算（可断言无信号计算副作用或直接快照比对）；force → `force_bypassed` 且 bypass 审计并存；`gate_off` 无"通过"表述；rollback 不产生记录；历史 Agent 查询为空。验证：全部通过。
- [x] 4.2 既有回归显式收集：绑定 + 覆盖套件、agent 版本化 API、评测任务 API、评测服务、分层（`versioning.py` 改动仅增不改）。验证：全部通过，既有断言零放宽。
- [x] 4.3 共享检查 `bash scripts/dev.sh check all`。验证：0 错误。
- [x] 4.4 汇总实际结果到 `verification.md`（命令输出、收集数、迁移实跑情况、边界与保留限制）。验证：tasks 全勾；PR 描述含实际结果。


## 5. 复核修正

- [x] 5.1 修正历史与发布入口租户边界；显式 system-scope 才可跨租户，缺 workspace 不提权。验证缺 workspace 的四种身份、显式系统身份及跨 workspace 发布。
- [x] 5.2 判定前冻结配置与 Run 身份；仅记录参与回归的 baseline；重复发布也追加，历史读取不计算门禁。
- [x] 5.3 outcome 的 ORM/迁移 CHECK 与读 schema 四值约束；分页参数校验；真实 SQL 约束失败覆盖普通/force 发布及审计回滚。
- [x] 5.4 迁移保留凭据（含软删除）；补持久化迁移护栏测试；真实 PG 验证无回填、空表降级/重升级及有证据时降级拒绝。
- [x] 5.5 记录最终共享检查、相关回归与迁移实测结果。
