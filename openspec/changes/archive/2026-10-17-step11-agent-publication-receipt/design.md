# Design

## Context

#270/#273 后发布判定可信，但结论不可追溯：`publish_version` 移指针、报告只进响应；唯一持久审计是 force 分支的 `_record_gate_bypass`（`AuditLogModel`）。已核实：`publish_version` 的事务边界是单个 `db.flush()` 序列（bypass 审计 → 指针移动 → flush），API 层经 `AuthContext.user_id` 提供 operator；`agent_versions.py` 路由文件已有 list/get/publish/rollback 模式；模型先例在 `models/agent_version.py`；迁移 head 为 `018_evaluation_scope`。

## Goals / Non-Goals

**Goals:** 每次成功发布的只追加凭据（与指针同事务、Run 身份与配置快照锚定、outcome 四值）；只读分页历史查询（workspace 隔离、快照不重算）；既有 bypass 审计与 require 拒绝语义不变。

**Non-Goals:** Deployment/激活、签名、灰度、回滚联动、前端、外部 evaluator；评测引擎/Task-Run 接口/Runtime/durable/conftest/feature-inventory 改动；`gate_off` 发布的任何"质量背书"语义。

## Decisions

### D1: 独立 `AgentPublicationModel`，凭据与指针同事务

`models/agent_version.py` 新增 `AgentPublicationModel`（表 `agent_publications`）：`workspace_id`、`agent_id`、`version`、`agent_config_hash`（写入时对齐该版本 `content_hash`，便于 Run 软删后仍可核验）、`candidate_run_id`/`candidate_run_hash`/`baseline_run_id`/`baseline_run_hash`（Run id + 各自 `agent_config_hash`，无则 NULL）、`gate_config`（发布时 `agent.evaluation_gate` 原始快照，可为 NULL）、`gate_report`（`_gate_report` 快照，`gate_off` 时为 NULL 或最小报告）、`outcome`（String，四值封闭——DB CHECK 与 Pydantic Literal 双重校验）、`operator_user_id`、`created_at`（继承 BaseModel）。索引：`(agent_id, created_at desc)` 与 `(workspace_id, agent_id)`。

独立表而非塞进 `AuditLogModel.metadata_` JSON：凭据有结构化查询需求（分页、按 Agent、字段白名单）与独立生命周期（只追加、永不清理策略与审计一致但语义不同——审计是行为流水，凭据是证据实体）。

### D2: 写入顺序与事务原子性

`publish_version` 内：bypass 审计（既有，force 时）→ 构造并 `db.add(publication)` → `agent.published_version = version` → 单次 `await self.db.flush()`。SQLAlchemy 同会话同事务：publication 的 DB 约束失败（如外键/长度）使 flush 抛错，整个事务回滚，指针不生效——满足"凭据失败发布不生效"。不做两阶段或显式 savepoint：单会话内 flush 失败即整体失败是既有模式（commit 路径同样语义）。门禁原始配置在解析与 await 前 deepcopy；Run id/hash 从 `_latest_completed_run` 已返回的对象在门禁计算前冻结，绝无二次查询。baseline 仅在启用回归比较时读取并记录，未消费的基线为空；报告 deepcopy 入库。重复发布当前版本同样新增凭据。

outcome 推导：`gate_config.mode == "off"`（含未配置）→ `gate_off`；`blocking and force` → `force_bypassed`；`mode == "warn"`（放行）→ `warn_passed`；否则（require 全过）→ `passed`。warn 模式下信号失败但放行仍为 `warn_passed`（如实反映"warn 放行"而非"评测通过"）。

### D3: 查询端零重算、快照直读

`AgentVersionService.list_publications(agent_id, workspace_id, is_system_scope, limit, offset)`：显式按 workspace 校验 Agent 归属；只有 `AuthContext.is_system_scope` 允许跨 workspace，缺 workspace 的 JWT/SSO/LDAP/workspace API key 一律 not found，不能推导为系统身份。发布 API 也在任何门禁、审计和凭据写入前传入可信 AuthContext 校验归属；既有内部 service 调用不传 context 的方式保留为受信调用；`order_by created_at desc, id desc`；返回 Pydantic 白名单 schema。API：`GET /api/agents/{agent_id}/publications?limit=&offset=` 挂在既有路由文件，`AuthContext` 校验照旧。刻意不做游标分页（created_at+id 双键即可稳定），limit 默认 20、范围 1..100，offset ≥0；API 非法参数返回 422——与仓库其他列表端点的朴素分页一致。

### D4: 回归主断言

发布 → 改 `evaluation_gate` 配置 + 改任务 + 补跑新高分 Run → 历史记录的 outcome/Run ID/报告快照逐字节不变；flush 失败注入（约束违反）→ 发布整体失败无指针移动；require 拒绝 → 无记录；force → `force_bypassed` + bypass 审计并存；`gate_off` → outcome 正确且无"通过"字样；跨 workspace 404；分页；rollback 后无新记录；历史 Agent 查询为空列表。

## Risks / Trade-offs

- [`evaluation_gate` 快照与报告快照的体积] → 报告含 20 条 missing_samples 上限（#273 已限），gate_config 是小 JSON；不做截断。
- [并行 worktree 迁移竞争] → 锚 `018`；step6/step14 若先合入迁移，rebase 后改锚最新 head 并复查 `alembic heads` 单一（上轮 #270→#273 已演练过此情形）。
- [outcome 脏值] → ORM/迁移使用同名 CHECK，读 schema 使用 Literal 四值验证。
- [发布高频 Agent 的凭据增长] → 只追加是设计意图（证据留存策略与审计一致）；不做清理，保留策略归后续治理。

## Migration Plan

单迁移 `019_agent_publication_receipt`：建表 + 两索引，锚 `018_evaluation_scope`，无数据回填（历史发布不补造），仅空表允许 downgrade 删表；存在任意凭据（含软删除）时拒绝 downgrade，保留证据及迁移版本。可变指针和通用审计无法重建普通发布的完整判定，凭据不可视为可弃派生数据。

## Open Questions

（无——事务边界、outcome 推导、查询形状均已按既有实现核实。）
