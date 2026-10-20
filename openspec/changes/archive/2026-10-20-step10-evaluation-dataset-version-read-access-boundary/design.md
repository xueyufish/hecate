# Design

## Context

#287(Run)与 #290(Dataset)建立并落地了同一套读边界模式:归属优先的一致 404(`_run_not_found`/`_dataset_not_found`,envelope `code: "NOT_FOUND"`、消息恒定不回显 ID)、`ensure_workspace_role(ctx, WorkspaceRole.VIEWER)` → `_forbidden`、`ctx.is_system_scope` 作用域门。版本接口([dataset_versions.py](../../../../src/hecate/ops/api/dataset_versions.py))已把 `ctx.workspace_id` 传入 service,但:全文无角色检查;`_get_version_or_404` 只做版本查找 + 路径绑定校验,不查父 Dataset(且 `list_versions`/`get_version` 的 service 路径也不查);404 消息拼接 `version_id`;diff 只捕获 `DatasetNotFoundError`,`against` 不可见时 service 抛 `DatasetVersionNotFoundError` → 500。service 侧 `workspace_id=None` 语义为不过滤(`list_versions`、`get_version`、`_get_dataset` 同);`_get_dataset` 查 `~deleted` 但仅被 create/checkout/diff 调用。`_get_version_or_404` 被 GET detail、DELETE、checkout 共用。

## Goals / Non-Goals

**Goals:**

- 三个 GET 获得与 Run/Dataset 边界同构的父 Dataset → 版本绑定 → 角色的三级校验;404 类内同体、零 ID 回显;diff 目标不可见 500 → 404。
- 复用 #290 的 dataset 读取 helper 作为父 Dataset 门,平台新增代码最小。

**Non-Goals:**

- 不改 `_get_version_or_404`、create/delete/checkout 写路径及其消息;不改 service/ORM/schema;不触及发布门禁、前端、Runtime。

## Decisions

### D1:父 Dataset 门复用 #290 的 dataset 读取 helper

`dataset_versions.py` 导入同包 `hecate.ops.api.evaluation` 的 `_get_dataset_for_read(ctx, dataset_id, db)`(存在 + `~deleted` + workspace/scope → 否则 `_dataset_not_found()`),作为三个 GET 的第一级校验。语义与 #290 完全同源:父 Dataset 不可见的 404 与 Dataset 详情接口同体("Dataset not found"),不重复实现边界、不产生第二种消息形状。

- 备选:在本模块复制一份 dataset 查询——被否决:同一边界两处实现必然漂移。
- 备选:改 service `_get_dataset` 为公共读取入口——被否决:service 层无 AuthContext/system 语义,且写路径共用它,越界。

### D2:版本级校验走独立读取 helper,`_get_version_or_404` 原样保留

新增 `_get_version_for_read(svc, ctx, dataset_id, version_id, db)`:经 D1 父 Dataset 门后,`svc.get_version(version_id, workspace_id=作用域过滤)` + `version.dataset_id == dataset_id` 绑定校验,任一失败抛新增的 `_version_not_found()`(envelope 同形,恒定消息 `"Dataset version not found"`,零 ID)。detail 及 diff 的源版本、UUID 目标版本均经它校验;DELETE/checkout 继续用 `_get_version_or_404`(含其既有消息),写路径行为与消息不变。

- 消息分两类且类内同体:父 Dataset 不可见 → "Dataset not found";版本级不可见(含 diff 目标)→ "Dataset version not found"。两类各自恒定,不泄露任何存在性差异(探测跨 workspace Dataset 时与探测不存在 Dataset 得到同一响应,与 #290 一致)。

### D3:service 异常一律弃用 `str(exc)`

diff 端点在既有 `DatasetNotFoundError` 之外补捕获 `DatasetVersionNotFoundError`,前者映射到 `_dataset_not_found()`，后者映射到 `_version_not_found()`——`svc.diff` 内部的 `against` 校验消息(拼 ID)永不进入响应。非法 `against`(非 UUID 且非 `live`)的 422 路径原样保留。

### D4:作用域过滤在 API 层收口

与 #290 相同：先验证资源归属，再用 `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)` 检查角色，角色错误映射为 `_forbidden`。缺 workspace 的非 system 身份在父 Dataset 门被拒绝，不能将 `None` 作为全局过滤传给版本 service；system scope 传 `workspace_id=None`,其余传 `ctx.workspace_id`。列表端点顺序:父 Dataset 门 → 角色 → 作用域化 `svc.list_versions`;detail 顺序:父 Dataset 门 → 版本绑定 → 角色;diff 顺序:父 Dataset 门 → 源版本绑定 → 解析 against → 目标版本绑定（against=live 时复用已验证的父 Dataset）→ 角色 → 比较计算。两侧资源归属均先于角色；service 在计算时再次查找资源并映射并发删除造成的 not-found。

### D5:新增版本读取授权矩阵

新增 `tests/test_api/test_evaluation_dataset_version_access_boundary.py`,镜像前两套边界测试的夹具模式(`_workspace_with_role`/`_client_as`/`_system_context` 等);覆盖 spec 全部 9 个场景,重点:软删 Dataset 的版本不可读、diff 目标四种不可见 + 500→404、422 保留、双 workspace 分页、零 ID 回显断言(含 404 body 中不出现任何 UUID)、拒绝请求只读。回归面:`test_publish_gate_versions.py`(service 不改,应保持绿)、`tests/test_api/test_publish_gate_version_api.py` 中已有的 `TestDatasetVersionEndpoints` 创建/读取/列表/删除/checkout/diff 用例，以及 `tests/test_api` 全量。

## Risks / Trade-offs

- [diff 500 → 404 是可观察行为变更] → 即本 change 的目的;spec 场景显式钉死,现有调用方对不可见目标的旧行为是错误响应,无兼容价值。
- [`_get_version_or_404` 与新 helper 并存] → 写路径保留旧消息(含 ID 回显)是已知不一致,属写路径边界后续切片;本 change 以 helper 隔离保证不隐式收紧写行为。
- [漏掉 diff 目标归属的角色顺序] → 新增 viewer/无角色 × 源/目标 × 缺失/已删除/跨 Dataset/跨 workspace 矩阵，保证不可见目标先返回 404。

## Migration Plan

纯 API 层授权收紧，无数据迁移，成功响应 schema 不变。不可见父 Dataset 的列表由 200 空列表收紧为 404，diff 不可见目标由 500 修复为 404；这些失败响应语义变化在 delta spec 中明确。回退 = 代码回滚,无持久状态需要恢复。

## Open Questions

(无)
