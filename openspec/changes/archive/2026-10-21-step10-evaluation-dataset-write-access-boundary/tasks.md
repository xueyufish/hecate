# Tasks

## 1. 写边界实现(`src/hecate/ops/api/evaluation.py`)

- [x] 1.1 `PUT`/`DELETE` 改造:`_get_dataset_or_404` 调用迁至 `_get_dataset_for_read(ctx, dataset_id, db)` + `ensure_workspace_role(ctx, WorkspaceRole.EDITOR)` → `_forbidden`;service 调用与级联行为原样;`_get_dataset_or_404` 定义保留不变。验证:`git diff` 不含其定义与 `delete_dataset` service 变化。
- [x] 1.2 `POST` 改造:先检查 active workspace,system 或非 system 缺失时统一返回 403 `NO_WORKSPACE`(恒定 envelope);再 `ensure_workspace_role(ctx, WorkspaceRole.EDITOR)` → `_forbidden`;通过后以 `ctx.workspace_id` 调用既有 `svc.create_dataset`(签名不变)。验证:缺 workspace 身份创建返回 NO_WORKSPACE,不再产生零 UUID Dataset。
- [x] 1.3 全部 404/403 响应体零资源 ID 回显。验证:实现审查 + 测试断言覆盖。

## 2. 写边界测试(新增 `tests/test_api/test_evaluation_dataset_write_boundary.py`)

- [x] 2.1 镜像既有边界测试夹具,覆盖 spec 全部场景:双 workspace 隔离(不可见 Dataset 的 PUT/DELETE 同体 404 零 ID)、viewer/无角色 → 403 且内容与删除状态不变、editor/admin 正常(update 语义保持、DELETE 204)、system 跨 workspace update/delete、创建的 editor 地板与 viewer 拒绝、`NO_WORKSPACE`(含 system)且不产生零 UUID Dataset、新 Dataset 归创建身份 active workspace、合法 DELETE 级联全部样本(含历史零 UUID)、拒绝请求用全列数据库快照验证零副作用。验证:新文件全部通过。
- [x] 2.2 404/403 响应体零资源 ID 回显断言;级联后样本与 Dataset 的删除标记断言。验证:测试显式覆盖。

## 3. 既有回归(`tests/test_api/test_evaluation_api.py` 等)

- [x] 3.1 排查既有 dataset CRUD 用例(ADMIN 身份应保持绿);viewer/无角色创建类断言按新语义修正属预期。验证:`python -m pytest tests/test_api/test_evaluation_api.py tests/test_api/test_evaluation_dataset_write_boundary.py tests/test_api/test_evaluation_dataset_access_boundary.py -q` 全绿。

## 4. 回归与变更验证

- [x] 4.1 运行 `python -m pytest tests/test_api -q -n 4` 全量。验证:0 failed。
- [x] 4.2 运行 `openspec validate step10-evaluation-dataset-write-access-boundary --strict` 与共享检查(`scripts/dev.py check all`:ruff check/format 于共享检查范围,mypy 于 `src/ packages/ scripts/dev.py`;记录实际结果)。验证:全部通过,`git diff` 不含 service/ORM/schema、版本/样本接口、报告 API 或前端变更。

## 5. 审查补充

- [x] 5.1 修正 design 与 tasks 的 POST 校验顺序,明确 env bootstrap key 缺 workspace 时同样拒绝创建。
- [x] 5.2 加强完整错误 envelope、角色/可见性矩阵、全列拒绝快照、客户端归属注入、system 与 editor/admin 成功路径、混合 workspace 样本级联和 PUT 原有语义覆盖。
- [x] 5.3 运行相关回归、共享 check all、OpenSpec strict 与 diff 检查,记录实际结果至 verification.md。
