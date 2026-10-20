# Design

## Context

#287(change `step10-evaluation-run-read-access-boundary`)已为四个 Run 读接口建立共享读边界的实现与规范模式:`_run_not_found()` 一致 404 envelope、归属优先于角色的检查顺序、`ensure_workspace_role(ctx, WorkspaceRole.VIEWER)` → `_forbidden` 与 `ctx.is_system_scope` 作用域门。三个 Dataset 只读接口尚未套用:列表未传 workspace 过滤,detail/items 走的 `_get_dataset_or_404` 只查 `~deleted` 且被 Dataset 更新/删除、条目添加/删除和 Run 创建等写路径共享。`EvaluationDatasetService.list_datasets` 已支持 `workspace_id: UUID | None` 过滤,`None` 语义为"不过滤"。

## Goals / Non-Goals

**Goals:**

- 三个 GET 接口获得与 Run 读边界同构的 workspace/角色边界;一致 404 不回显资源身份。
- 复用 #287 的 helper 与测试夹具模式,新增平台代码最小化。

**Non-Goals:**

- 不改 Dataset 写路径(create/update/delete/items 写)、Run 创建、版本管理、前端。
- 不改 `_get_dataset_or_404` 及任何 service/ORM/schema 签名;不引入新的权限抽象。

## Decisions

### D1:镜像 Run 读边界的实现形态,不动共享 helper

新增读取专用 helper `_get_dataset_for_read(ctx, dataset_id, db)`(归属 + `~deleted` → 命中返回模型,否则抛 `_dataset_not_found()`——新增的与 `_run_not_found()` 同形的一致 404,`code: "NOT_FOUND"`,不回显 ID);detail 与 items 端点先经它做归属,再在端点内 `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)` → `_forbidden`。角色检查留在调用方,保持与 Run 侧"归属优先于角色"相同的顺序语义。

- 备选:改 `_get_dataset_or_404` 增加 ctx 参数——被否决:该 helper 被 6 个写调用点共享,顺带改写属越界;写路径边界不在本 change 范围。
- 备选:在 service 层加 `get_dataset_for_workspace`——被否决:边界是 API 层职责,service 无 AuthContext,传入 workspace_id 即可表达,与 Run 侧实现层位一致。

### D2:列表作用域在 API 层收口,`None` 永不穿透

`list_datasets` 端点先按身份决定过滤:非 system 且无 active workspace → 在进入 service 前直接拒绝(403,与缺角色同路径);`ctx.is_system_scope` → 不传过滤(既有全局语义);否则传 `ctx.workspace_id`。这样 service 的 `workspace_id=None`"不过滤"语义只对显式 system scope 可达,消除了 None 误传导致的全局列表退化。

### D3:items 端点复用 detail 的归属结果

`list_items` 先经 `_get_dataset_for_read` 解析归属(同一 404 语义),角色检查后调用既有 `svc.list_items(dataset_id, ...)`——dataset 归属已核验,items 无需二次过滤,既有筛选/分页代码零改动。

### D4:测试镜像 #287 夹具,单文件聚焦

新增 `tests/test_api/test_evaluation_dataset_access_boundary.py`,复用 `test_evaluation_run_access_boundary.py` 的 `_workspace_with_role`/成员与 system-scope 夹具模式;回归 `tests/test_api/test_evaluation_api.py`；不预设既有用例必然依赖全局可见性，只有实际回归发现旧假设时才调整断言。

## Risks / Trade-offs

- [既有 dataset API 测试假设跨 workspace 可见] → 属预期破坏;逐用例按新边界修正,全量评测 API 回归必须绿。
- [`None` 过滤误传造成全局列表] → D2 在 API 层拦截;测试覆盖缺 workspace 身份不退化。
- [写接口保持旧行为造成读写边界不一致] → 如实保留;写路径边界属后续 change,本 change 的 helper 隔离保证不隐式收紧写行为。

## Migration Plan

纯 API 层收紧,无数据迁移、无契约版本变更。回退 = 代码回滚,无持久状态需要恢复。

## Open Questions

(无)


## 审查补充

- Dataset 列表使用现有 service 的 workspace 过滤；三条读取路径的权限实现符合要求，无需进一步修改 API 或 service。
- 缺 workspace 测试必须针对已存在且包含条目的真实 Dataset，不能仅请求随机不存在 ID。补验覆盖 JWT/workspace API key 以及缺角色/缓存 viewer role。
- 列表分页使用交错创建时间的双 workspace 数据，逐页核对精确 ID 和 total，并验证已删除记录、耗尽页及 system 全局读取。
- 条目 tags 沿用分页后过滤及不按 tags 缩减 total 的历史行为；known_bad 沿用 SQL 过滤后计数/分页。未命中 tag 的可见非空 Dataset 可以返回空 items 与非零 total，不在本 change 顺带修正 service 行为。

- 完整 API 回归发现 calibration 测试的时间数据不稳定：人工评分在 `_NOW +100..109` 秒，若默认报表窗口在整点后不足 100 秒结束，全部人工评分处于未来，pair_count=0。单独普通时钟重跑可通过；通过内存加载 HEAD 原 evaluation API 并将报表时钟固定到整点后 30 秒，再次复现相同失败，排除本次读取权限变更。仅将该测试文件的 `_NOW` 前移一小时，保留全部统计断言；普通回归和相同整点时钟均通过。
