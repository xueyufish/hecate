# Proposal

## Why

#287(Run 读边界)与 #290(Dataset 读边界)之后,同一治理线的 Dataset 版本接口仍是缺口:`GET /datasets/{id}/versions`、`GET .../versions/{version_id}`、`GET .../versions/{version_id}/diff` 传入了 `ctx.workspace_id` 但没有任何角色检查;`list_versions`/`get_version` 不校验父 Dataset 的存在与删除状态(软删 Dataset 的版本仍可读取);404 消息直接拼接资源 ID(`_get_version_or_404` 与 service 异常均回显);diff 对 `against` 缺失或跨 Dataset 只捕获 `DatasetNotFoundError`,service 抛出的 `DatasetVersionNotFoundError` 未映射 → **500**。service 的 `workspace_id=None` 语义为"不过滤",缺 workspace 身份可借此获得全局读取。

## What Changes

- **三个 GET 接口落地共享读边界**(与 #287/#290 同构):先校验父 Dataset 归属与删除状态,再校验版本存在、未软删且属于路径中的 Dataset(diff 两侧均验证),全部归属通过后检查最低 `viewer` 角色;归属优先于角色,跨 workspace 一律 404。
- **统一无 ID 404**:不存在、已软删、跨 workspace、版本与 Dataset 不匹配、diff 目标不可见,全部返回不回显资源 ID 的一致 404 envelope(资源类内同体;父 Dataset 类沿用 #290 的 "Dataset not found",版本类用 "Dataset version not found");diff 目标不可见由 500 修复为 404。
- **身份规则**:显式 system scope 跨 workspace;缺 active workspace 的非 system 身份在进入 service 前拒绝,不经过 `None` 获得全局读取。
- **既有语义不变**:非法 `against` 保持 422;成功响应形状、版本分页、内容摘要与 diff 比较算法原样;可见 Dataset 无版本返回 200 空列表;三个接口保持只读。
- **写路径不动**:create/delete/checkout 共用的 `_get_version_or_404` 及其消息、service/ORM/schema、前端、Runtime 均不在范围内。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `dataset-versioning`: 新增「Dataset version read access boundary」需求,约束三个只读版本接口的父 Dataset 归属、版本-Dataset 绑定、viewer 角色下限、一致无 ID 404(含 diff 目标 500→404 修复)与身份规则;既有成功语义场景在边界内保持为真,无需修改。

## Impact

- `src/hecate/ops/api/dataset_versions.py`:新增版本读取专用校验与统一 404;复用 #290 的 dataset 读取 helper 作为父 Dataset 门;三个 GET 改造;`_get_version_or_404` 及写端点原样。
- 测试:新增 `tests/test_api/test_evaluation_dataset_version_access_boundary.py`(补充版本读取授权矩阵，与既有 TestDatasetVersionEndpoints 互补);`tests/test_services/test_evaluation/test_publish_gate_versions.py`(service 层,不改 service 应保持绿)与 `tests/test_api` 全量回归。
- 不改 service/ORM/schema、发布门禁、前端;无文档同步需求(纯租户隔离收敛)。
