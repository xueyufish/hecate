# Proposal

## Why

发布门禁经过 #270（版本绑定）与 #273（证据完整性）后，判定时的证据是可信的，但**判定结论本身不可追溯**：`publish_version` 移动 `published_version` 指针并把门禁报告放进响应返回，不落任何持久记录；发布路径唯一的审计写入是 force 绕过分支的 `_record_gate_bypass`。事后无法回答"当时为什么允许发布"——candidate/baseline Run 是发布时点选中的、`evaluation_gate` 是可变配置、评测可以补跑，三者事后都可能不同。

演进方案 step11 与验收矩阵要求"发布配置与证据能够完整重建"；本 change 把每次成功发布的证据凭据固定为只追加记录。

## What Changes

1. **只追加的发布记录**：新模型 `AgentPublicationModel`（表 `agent_publications`）——workspace、agent_id、version、`agent_config_hash`（对齐版本 content_hash）、本次判定实际选中的 `candidate_run_id` 与 `baseline_run_id` 及各自 `agent_config_hash`、发布时的 `gate_config` 快照、门禁报告快照、outcome（封闭枚举 `passed` / `warn_passed` / `force_bypassed` / `gate_off`）、operator user_id、created_at。Run 身份取判定时实际选中者，不在记录时重新查询 latest。
2. **与发布指针同事务**：写入顺序为 bypass 审计（既有）→ publication 记录 → 移动 `published_version` 指针，同一事务提交；publication 写入失败则发布不生效。require 拒绝路径不产生任何成功发布记录。`gate_off` 的语义是"评测未参与判定"，记录与呈现均不得表述为"评测通过"。历史发布不补造凭据。
3. **只读历史查询**：`GET /api/agents/{agent_id}/publications`——按时间倒序分页、workspace 隔离（他 workspace 的 Agent 表现为不存在）、字段白名单（不含内部中间态）；读取历史不重新计算门禁，返回的是当时的报告快照。
4. **回归**：发布后修改门禁配置、修改任务、补跑评测——旧记录仍保持当时的 outcome、Run ID 与报告快照；记录写入失败与发布失败原子；require 拒绝无记录；force 绕过 outcome 为 `force_bypassed` 且审计并存；`gate_off` 不显示为通过；分页与 workspace 隔离。

**边界**：不做 Deployment/激活、签名、灰度、回滚联动（rollback 不动指针、不产生发布记录）、前端工作台或外部 evaluator；不修改评测引擎、Task/Run 控制接口、Runtime/durable、公共 conftest 或全局功能清单。文件边界：新增 publication 模型（入 `models/agent_version.py`）、服务逻辑并入 `AgentVersioningService`、API 挂到既有 `agent_versions.py` 路由文件、独立测试文件、增量迁移锚当前唯一 head。

## Capabilities

### New Capabilities

（无——发布凭据是既有 `agent-versioning` 能力的证据持久化，不引入新行为能力。）

### Modified Capabilities

- `agent-versioning`: MODIFIED「发布与发布门禁挂钩」——每次成功发布 SHALL 落一条只追加凭据记录（与指针同事务、Run 身份与配置快照锚定、outcome 四值封闭）；ADDED「发布历史只读查询」要求——分页、workspace 隔离、字段白名单、读取不重算门禁、历史不补造。

## Impact

- **代码**：`src/hecate/models/agent_version.py`（新 ORM 模型 + Pydantic 读 schema）、新迁移（表 + 索引，锚 `018_evaluation_scope`）、`src/hecate/studio/agents/versioning.py`（发布事务内写记录 + 查询方法）、`src/hecate/studio/api/agent_versions.py`（GET publications 路由）。
- **测试**：新独立测试文件（发布凭据 + 历史查询语义）；既有版本化/门禁回归显式收集。
- **规范**：`openspec/specs/agent-versioning/spec.md`（经本 change delta 归档时同步）。
- **不涉及**：与 `step14-run-action-query`（task_control/channel 域）和 `step6-accepted-action-dispatch`（runtime/durable 域）零文件交集；迁移锚 `018`（当前唯一 head），rebase 时若并行分支先合入新迁移则改锚最新 head 并复查单 head。
