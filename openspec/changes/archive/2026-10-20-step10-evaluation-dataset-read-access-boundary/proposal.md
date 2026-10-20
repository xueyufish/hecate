# Proposal

## Why

#287 已为四个评测 Run 读接口落地共享读边界,但三个评测 Dataset 只读接口仍接收 `AuthContext` 而不使用 workspace 或角色:`GET /api/evaluation/datasets` 返回跨 workspace 的全局列表,`GET /api/evaluation/datasets/{dataset_id}` 与 `GET /api/evaluation/datasets/{dataset_id}/items` 对任意已认证身份暴露其他 workspace 的 Dataset 存在性、元数据与条目内容。这是 step10 租户隔离线上的明确缺口,与同族 Run 读边界行为不一致。

## What Changes

- **三个 GET 接口落地共享读边界**(与 #287 的 Run 读边界同构):列表条目、`total` 与分页限定在调用方 workspace;detail 与 items 先做归属校验(不存在、已软删、跨 workspace → 完全一致的 404,不回显资源 ID、不区分条件),再检查最低 `viewer` 角色(归属优先于角色,跨 workspace 一律 404 而非 403)。
- **身份规则**:显式 system scope 可跨 workspace;缺 active workspace 的非 system 身份必须拒绝,不得退化为全局查询(service 的 `workspace_id=None` 是"不过滤"语义,API 层必须先行拦截)。
- **既有语义不变**:成功响应形状、条目筛选(`tags`/`known_bad`)与分页行为保持原样;三个接口保持只读;`_get_dataset_or_404` 及全部 Dataset 写路径、Run 创建、版本管理、前端不在范围内。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-dataset`: 新增「Evaluation dataset read access boundary」需求,约束三个只读 Dataset 接口的 workspace 归属、viewer 角色下限、一致 404 与身份规则;既有成功语义场景在边界内保持为真,无需修改。

## Impact

- `src/hecate/ops/api/evaluation.py`:新增 dataset 读取专用 helper(归属 + 一致 404)与各端点角色检查;`list_datasets` 按 scope 传入 workspace 过滤。
- 测试:新增 `tests/test_api/test_evaluation_dataset_access_boundary.py`(镜像 `test_evaluation_run_access_boundary.py` 夹具);回归 `tests/test_api/test_evaluation_api.py` 及既有评测读取测试；只有实际发现旧可见性假设时才调整原有用例。
- 回归稳定性修复:`tests/test_api/test_evaluation_annotations_api.py` 的种子时钟移至上一小时，避免人工评分在整点后短时间内处于未来；仅测试数据调整，不修改 calibration 生产逻辑或原有统计断言。
- 不改 `EvaluationDatasetService`/ORM/schema、`_get_dataset_or_404` 及写接口行为;无前端、无文档同步需求(纯租户隔离收敛)。
