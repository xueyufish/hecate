# Design

## Context

三个 Dataset 版本写接口尚未对写入角色建立完整边界。创建将 `ctx.workspace_id` 传给服务,在无 workspace 身份下可触发零 UUID 归属;删除/checkout 缺父数据集边界。已有读接口提供 `_get_dataset_for_read`、常量 404 与归属先于角色的模式,本 change 沿用该契约收敛写入口。

## Goals / Non-Goals

- 非 system 身份仅可写入当前 workspace,且最低角色为 editor。所有引用资源均须在角色判断前完成可见性与绑定检查。
- system 可以跨 workspace 操作,服务调用和版本查询始终绑定目标 Dataset 的真实 workspace。
- 保留冻结内容/hash、重名 409、软删除与名称保留、checkout 算法及响应形状。
- 不修改版本服务算法、普通 Dataset 写接口、读接口、前端或 Runtime,不迁移历史数据。

## Decisions

1. **创建使用 `_authorize_dataset_write`。** 先由 `_get_dataset_for_read` 解析父数据集,不存在/已删除/跨 workspace 返回无标识 Dataset 404,再要求 editor;同 workspace 的 viewer/无角色身份返回 403。无 active workspace 的非 system 身份不能越过父级边界。
2. **删除/checkout 使用 `_get_version_for_write`。** 顺序为父数据集 → `svc.get_version(version_id, workspace_id=ds.workspace_id)` → `version.dataset_id` 绑定 → editor。缺失、软删除、异 workspace、异 Dataset 版本均在角色检查前返回无标识 Version 404。即使 system 也按父数据集的 workspace 查询版本,避免接受归属不一致的历史记录。
3. **所有写入传 `ds.workspace_id`。** system 的跨 workspace 权限只豁免调用者的 workspace/角色限制,不豁免 Dataset 与版本的归属一致性。新冻结版本和 checkout 恢复条目落入目标 Dataset 的 workspace。
4. **规范化服务层再次查询的失败。** 创建/checkout 的 `DatasetNotFoundError` 映射 Dataset 常量 404;删除/checkout 的 `DatasetVersionNotFoundError` 映射 Version 常量 404。预检查后资源失效也不会落为 500 或回显资源 ID。
5. **测试通过真实 API 与数据库状态验证边界。** 复用 `client`/`db_session` 夹具与身份 override,覆盖 editor/admin/viewer/无角色/无 workspace/system。拒绝路径比较 Dataset、Version、Item 全表状态;成功路径验证归属、冻结不变性、checkout 实际恢复内容及软删除名称保留。

## Risks / Trade-offs

- 写角色收紧会拒绝此前越权成功的 viewer/无角色请求,这是本 change 的目标行为。
- 历史零 UUID 或其他 workspace 的版本不会自动修复;若与父 Dataset 归属不一致,写操作返回 Version 404,不做隐式归属迁移。正常 system 跨 workspace 写入仍成功。
- 保持服务算法不变,本次修复仅收敛 API 边界与错误映射;未引入额外事务锁或并发算法。

## Migration Plan

无 schema 迁移。实现单一 router 边界与回归测试,运行受影响 API/服务测试、共享检查与 OpenSpec 严格校验。回退可还原 router 改动。

## Open Questions

无。历史归属修复需另行设计,不在本 change 中扩展。
