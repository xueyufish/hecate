# Proposal

## Why

评测 Dataset 自身的三个写接口是评测域写授权线上最后一处未闭合的 Dataset 缺口:`PUT /api/evaluation/datasets/{dataset_id}` 与 `DELETE /api/evaluation/datasets/{dataset_id}` 只经 `_get_dataset_or_404` 检查存在性,无 workspace 归属校验、无角色检查——任何已认证身份可改/删**任意 workspace** 的 Dataset(DELETE 还会级联软删其全部样本);`POST /api/evaluation/datasets` 无写角色门槛,缺 active workspace 的身份(含 system)创建的 Dataset 经 service 的 `workspace_id or uuid.UUID(int=0)` 落到零 UUID 所有。#287/#290/#293/#295/#296/#299 已覆盖 Run/Dataset/版本/Task 读取与版本/样本写入。

## What Changes

- **PUT/DELETE 落地写边界**(与家族同构,角色地板 `editor`):先经共享读取门解析 Dataset(存在、未删除、归属;system 跨 workspace),归属通过后检查 `editor` 角色;归属优先于角色,不可见 Dataset 统一返回不回显资源 ID 的一致 404(沿用 "Dataset not found" envelope),本 workspace 的 viewer/无角色返回 403。
- **POST 收紧创建契约**:最低 `editor` 角色;**必须持有明确 active workspace(含 system),缺失返回 403 `NO_WORKSPACE`**;本切片不引入 workspace 选择字段,REST 不再创建零 UUID 归属的 Dataset——创建的 Dataset 归创建身份的 active workspace。
- **既有语义不变**:合法 editor DELETE 仍级联软删所属**全部**样本(含历史零 UUID 样本,级联不按样本 workspace 过滤);update 语义与成功形状原样;service 签名不变;读取 helper 与版本/样本接口零变化。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-dataset`: 新增「Evaluation dataset write access boundary」需求,约束 Dataset 创建/更新/删除的父级归属、editor 角色地板、一致无 ID 404、`NO_WORKSPACE` 创建契约与级联保持;既有 CRUD 场景在边界内保持为真,无需修改。

## Impact

- `src/hecate/ops/api/evaluation.py`:三个写端点改造(复用 `_get_dataset_for_read` 门 + 角色检查 + `NO_WORKSPACE` 403);service 零变化。
- 测试:新增 `tests/test_api/test_evaluation_dataset_write_boundary.py`(镜像既有边界套件夹具);`tests/test_api/test_evaluation_api.py` 的 dataset CRUD 用例以 ADMIN 身份运行应保持绿,viewer/无角色创建类用例(如有)按新语义修正属预期。
- 不改 service/ORM/schema、读取 helper、版本/样本接口、报告 API、平台 Task/Run、前端。
