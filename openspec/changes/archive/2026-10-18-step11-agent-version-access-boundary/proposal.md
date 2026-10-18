# Proposal: step11-agent-version-access-boundary

## Why

Agent 版本生命周期 API 的 13 个端点目前只做了认证、没有做一致的授权：除 publish（workspace 归属校验，#276 引入）和 publications（workspace 隔离查询）外，其余端点对 `AuthContext` 仅用作 `actor_user_id` 或完全忽略——workspace W1 的 viewer 可以读取、改名、删除甚至回滚 workspace W2 的 Agent 版本。发布门禁（评测证据、凭据）防的是"不合格版本被发布"，防不了"未授权身份触碰版本生命周期"；租户边界是平台安全底线，应在证据链之外独立成立。

## What Changes

- **版本生命周期全端点补齐授权**：13 个端点（list/detail/skill-closure/drift/publications/published/diff/version-status 8 个读 + commit/update/delete/publish/rollback 5 个写）统一在服务层执行两级校验：
  1. **workspace 归属**——非 system-scope 身份访问其他 workspace 的 Agent 时，一律表现为不存在（404，与 #276 publish 语义一致，不泄露存在性）；
  2. **最低角色**——读端点要求 VIEWER，写端点（含 rollback——它创建新版本）要求 EDITOR；不足时 403 且不改变任何状态。
- **服务层共享校验入口**：新增共享 helper（归属 + 角色一次判定），13 个生命周期方法统一接入；`auth_context` 在既有生命周期方法保持可选（省略 = 可信内部调用）；发布历史查询保留缺失身份时拒绝的原有约束，`channel`/`evaluation engine`/`workflow execution_service` 等仅使用 `resolve()` 的运行时解析路径不受影响。
- **publish/publications 语义收敛到同一 helper**：publish 在既有归属校验之上补齐 EDITOR 角色门槛；publications 的隔离查询行为保持不变（回归锚点）。system-scope 豁免沿既有 `ensure_workspace_role` 契约，仅显式 `is_system_scope`。
- **错误面归一**：跨 workspace → 404 `NOT_FOUND`；同 workspace 角色不足或缺 workspace 成员身份 → 403 `FORBIDDEN`（复用 `WorkspaceRoleRequiredError` 的既有映射契约）。

**边界（不做）**：不改 `resolve()` 运行时解析语义、评测/门禁判定、数据模型与迁移、前端；不扩展到版本生命周期之外的其他 studio/ops 端点（同类缺口在 verification 登记为已知限制，后续独立 change 处理）；不新增角色层级或权限模型。

## Capabilities

### New Capabilities

（无——访问边界是既有 `agent-versioning` 能力生命周期行为的授权约束，不引入新行为能力。）

### Modified Capabilities

- `agent-versioning`: ADDED「版本生命周期归属校验」——全部生命周期端点按 workspace 归属隔离，跨 workspace 一律不存在语义；ADDED「版本生命周期角色门槛」——读 VIEWER、写 EDITOR、system-scope 显式豁免，角色拒绝不改变任何状态；发布与发布历史入口在边界收紧后维持既有判定。

## Impact

- **代码**：`src/hecate/studio/agents/versioning.py`（共享校验 helper + 13 个生命周期方法接入可选 `auth_context`）；`src/hecate/studio/api/agent_versions.py`（全部端点传入 `auth_context=ctx`、捕获 `WorkspaceRoleRequiredError` 映射 403）。无新依赖、无迁移、无 `publish_gate.py` 改动。
- **测试**：新增独立访问边界测试文件（跨 workspace 读/写、viewer/editor/admin 矩阵、缺 workspace 身份、system-scope、publish/publications 回归锚点）；既有 `test_agent_versioning.py`、`test_agent_version_evaluation_binding.py`、`test_agent_publication_receipt.py` 回归（默认 client 身份为同 workspace ADMIN，预期零改动通过）。
- **规范**：`openspec/specs/agent-versioning/spec.md`（经本 change delta 归档时同步）。**存量缺陷修复**：该主 spec 第 126 行残留 `## ADDED Requirements` 增量头（先前归档同步遗留），其后 10 条 requirement 脱离 `## Requirements` 主节（归档解析器只认主节内 3 条）；新 CLI 已在本次 `openspec validate --strict` 中报告"Archive would refuse this delta"。本 change 归档前需机械修复（删除该残留头，使 10 条 requirement 回到主节），已列入 tasks。


## 审查澄清：发布历史查询保留既有缺省拒绝

list_publications 原本在缺少 workspace 身份时拒绝读取。统一 helper 不得把这一查询改成省略身份即全局放行：此入口保留 auth_context=None 时的 not found，可信内部查询也须显式携带身份（例如 system-scope）。其余既有生命周期方法的省略身份兼容路径不变。测试读取审查快照时使用显式 system-scope。
