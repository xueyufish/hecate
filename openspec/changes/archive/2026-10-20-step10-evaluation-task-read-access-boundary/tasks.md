# Tasks

## 1. 读取边界实现

- [x] 1.1 `tasks/service.py`:`list_tasks` 的 `workspace_id` 放宽为 `uuid.UUID | None`(非 None 才追加 workspace 条件,docstring 声明 None 为跨 workspace 仅限 system 读取路径);`list_task_runs` 保持原非 Optional 签名,API 传入已解析 Task 的真实 workspace,service 复用既有作用域解析,Runs 查询增加 `EvaluationRunModel.workspace_id == task.workspace_id`;`get_task`/`_get_task_or_raise` 零变化。验证:模块导入成功,`git diff` 不含二者定义变化。
- [x] 1.2 `evaluation_tasks.py`:新增 `_get_task_for_read(ctx, task_id, db) -> EvaluationTaskModel`(缺 workspace 的非 system 提前拒绝;只读解析 Task 存在、删除状态与归属;不可见 → 既有 `_not_found()`);`GET /tasks` 加 viewer 角色检查 + 作用域化调用;`GET /tasks/{task_id}` 与 `GET /tasks/{task_id}/runs` 改走 `_get_task_for_read` + 角色检查。验证:三个端点编译与导入成功。
- [x] 1.3 既有 envelope `_not_found()`、写端点(create/update/delete/enable/queue-run)与 `get_task`/`_get_task_or_raise` 零变化。验证:`git diff` 范围核对。

## 2. 边界测试(新增 `tests/test_api/test_evaluation_task_access_boundary.py`)

- [x] 2.1 镜像既有边界测试夹具,覆盖 spec 全部场景:双 workspace 列表隔离(total)、system 跨 workspace 列表/详情/Runs(system 修复验证:修正前列表为空)、缺 workspace 非 system 拒绝且不退化、detail 三态(缺失/软删/跨 workspace)同体 404 零 ID、Runs of invisible task 404、Run 脏关联防御(构造 workspace 不匹配的 Run,断言不进 items/total)、归属优先于角色(跨 workspace 无角色 → 404)、viewer/editor/admin 可读、本 workspace 无角色 → 403、分页与 type/status 过滤保持、拒绝只读、非 system 写路径正常且 system 写仍按现状拒绝(D1 不放宽钉死)。验证:新文件全部通过。
- [x] 2.2 404/403 响应体零资源 ID 回显断言。验证:测试显式覆盖。

## 3. 既有回归(`tests/test_api/test_evaluation_tasks_api.py` 等)

- [x] 3.1 排查既有 19 例在边界下的行为(非 system ADMIN 身份应保持绿;断言 system 行为的用例按修复后语义修正属预期)。验证:`python -m pytest tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_task_access_boundary.py -q` 全绿。

## 4. 回归与变更验证

- [x] 4.1 运行 `python -m pytest tests/test_api -q -n 4` 全量。验证:0 failed。
- [x] 4.2 运行 `openspec validate step10-evaluation-task-read-access-boundary --strict` 与共享检查(ruff check/format、mypy 于改动文件)。验证:全部通过,`git diff` 不含报告 API、平台 Task 控制、评分查询、写接口、Runner、Runtime 变更。


## 5. 复审修正

- [x] 5.1 原实现补测复现 service `EvaluationTaskNotFoundError` 未映射导致异常外泄;恢复 API 404 映射,不回显 Task ID。
- [x] 5.2 Run 列表传入已解析 Task 的真实 workspace,service 保持非 Optional 签名并移除重复 system 查询;为读取 helper 补充明确返回类型,拒绝缺 workspace 的非 system。
- [x] 5.3 补充完整 envelope、低角色下不可见 Task 三态、system/普通身份的 type/status 过滤及多页/越界分页、Run 真实绑定与 workspace/软删除条件、排序和 summary;快照直接查询全部 Task/Run 持久列。
- [x] 5.4 同步文档与实际设计,撤回“唯一未闭合的评测读取资源”表述。
- [x] 5.5 完成相关回归、共享 check all、OpenSpec strict、feature inventory、AST 范围核对;记录实际结果于 verification.md。
