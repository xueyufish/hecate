# Design

## Context

#287(Run)、#290(Dataset 读 + 样本读写)、#293(版本读)、#295(版本写)、#296(样本写)、#299(Task 读)之后,Dataset 自身的三个写接口是最后的 Dataset 缺口:`update_dataset`/`delete_dataset` 仅经 `_get_dataset_or_404`(存在 + `~deleted`,无归属、无角色,现仅剩这 2 个调用点);`create_dataset` 传 `ctx.workspace_id`,service 对 `None` 强制 `workspace_id or uuid.UUID(int=0)`(`dataset_service.py`),缺 workspace 身份(含 system)创建的 Dataset 落零 UUID。家族既有模式:`_get_dataset_for_read` 作用域门 + 端点内 `ensure_workspace_role` → `_forbidden`;`_get_item_for_write` 已复用该门。

## Goals / Non-Goals

**Goals:**

- PUT/DELETE 获得与家族同构的归属优先校验 + `editor` 角色地板;404 沿用 "Dataset not found" 恒定 envelope。
- POST 收紧:editor 地板 + 显式 active workspace 契约(`NO_WORKSPACE` 403),零 UUID 创建路径从 REST 消失。

**Non-Goals:**

- 不改 `delete_dataset` 级联语义(全部样本级联,不按样本 workspace 过滤——历史零 UUID 样本随 Dataset 一起删除)、`update_dataset` 签名、读取 helper、版本/样本接口、报告 API、平台 Task/Run、前端;不回填历史零 UUID Dataset。

## Decisions

### D1:PUT/DELETE 复用 `_get_dataset_for_read` 门 + 端点内 editor 地板

与 #296 样本写完全同构:端点先 `await _get_dataset_for_read(ctx, dataset_id, db)`(存在 + `~deleted` + workspace/scope → 否则既有 "Dataset not found" 404),再 `ensure_workspace_role(ctx, WorkspaceRole.EDITOR)` → `_forbidden`,随后调用既有 service。`_get_dataset_or_404` 在两个调用点迁走后成为无调用死代码——按既有先例保留定义不删除(避免本 change 夹带清理),其去留在 step19 或后续写边界切片按使用量处理。

- 备选:参数化 `_get_dataset_for_read` 的角色地板——被否决:该 helper 被 5 个读端点 + 1 个写 helper 共用,加参数扩大影响面。

### D2:POST 的显式 workspace 契约与 `NO_WORKSPACE` 403

顺序:workspace 存在性检查 → `ensure_workspace_role(ctx, WorkspaceRole.EDITOR)` → service。所有身份(含 system)缺 active workspace 时统一先拒绝为 403、code `NO_WORKSPACE`、恒定消息(envelope 形状与既有 403 一致);具备 active workspace 后,非 system 身份再检查 editor 角色,不足返回 FORBIDDEN,system 保留角色豁免。通过检查后传 `ctx.workspace_id`(恒非 None)给 service,`workspace_id or uuid.UUID(int=0)` 的回退分支从 REST 路径不可达。

- 语义区分:FORBIDDEN = "角色不足";NO_WORKSPACE = "没有可拥有新资源的 active workspace"。创建需要所有者,而本切片不引入 workspace 选择字段,故 system 无 workspace 时不能创建——有意的不对称(system 仍可跨 workspace 更新/删除已有 Dataset),由 spec 场景显式钉死。
- 备选:system 无 workspace 时自动落到某默认 workspace——被否决:隐式所有者选择属治理决策,不属于本切片。

### D3:service 零变化

`create_dataset` 的 `workspace_id or uuid.UUID(int=0)` 回退保留(service 直调方行为不变;REST 路径经 D2 后恒传非 None,零 UUID 创建从 REST 不可达)。`update_dataset`/`delete_dataset` 及级联逻辑原样——合法删除的"全部样本级联(含零 UUID)"由测试钉死为保持行为。

## Risks / Trade-offs

- [viewer/无角色此前可创建 Dataset] → 边界后 403 属预期修复;既有用例如覆盖需按新语义修正。
- [system 无 workspace 不能再经 REST 创建 Dataset] → 有意收紧;需要创建时使用带 active workspace 的身份(现有 env bootstrap key 的 AuthContext 不含 workspace,因此也会收到 NO_WORKSPACE;本切片不增加选择目标 workspace 的入口)。
- [`_get_dataset_or_404` 成为死代码] → 保留定义,注释现状;清理归 step19。

## Migration Plan

纯 API 层收紧,无数据迁移、无契约版本变更。回退 = 代码回滚;本 change 期间创建的 Dataset 带真实 workspace,回退后读写不受影响。

## Open Questions

(无)
