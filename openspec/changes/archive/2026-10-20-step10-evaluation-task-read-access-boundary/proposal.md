# Proposal

## Why

评测任务(EvaluationTask)的三个读取接口没有角色检查,是 step10 评测资源读取授权中的一处缺口(评分查询、标注和其他报告入口仍需独立治理):`GET /api/evaluation/tasks`、`GET /tasks/{task_id}`、`GET /tasks/{task_id}/runs` 均无 `viewer` 角色地板。此外两个具体缺陷:system scope 的 `workspace_id=None` 被 service 直接放进 `==` 条件(SQL `IS NULL`),导致 system 身份列表恒空、详情恒 404——system 读取目前完全不可用;Task 的 Run 列表只按 `task_id + ~deleted` 过滤,不约束 Run 自身的 workspace,错误关联到该 Task 的其他 workspace Run 会进入结果与 total。

## What Changes

- **三个读取接口落地共享读边界**(与 #287/#290/#293 同构):普通身份最低 `viewer`;详情与 Run 列表先解析 Task(存在、未删除、workspace 归属)再检查角色,归属优先于角色;不可见 Task 及其 Runs 统一返回不回显资源 ID 的一致 404(沿用既有 "Evaluation task not found" envelope)。
- **修复 system scope 读取**:`workspace_id=None` 只在读取路径表示跨 workspace,不再进入 `==` 条件;system 可跨 workspace 列表与读取;读取指定 Task 的 Runs 时使用该 Task 的真实 workspace 约束 Run。
- **Run 列表完整性**:Runs 同时按 `task_id`、workspace(取 Task 真实 workspace)与删除状态过滤,`items` 与 `total` 使用相同条件。
- **写路径零变化**:`get_task`/`_get_task_or_raise` 的既有作用域语义与签名保持,update/delete/enable/queue-run 等写接口行为不变;缺 workspace 的非 system 身份在进入无作用域查询前被角色检查拒绝。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-tasks`: 新增「Evaluation task read access boundary」需求,约束三个读取接口的角色地板、归属优先、一致无 ID 404、system scope 修复与 Run workspace 完整性;既有 CRUD/执行/生命周期场景在边界内保持为真,无需修改。

## Impact

- `src/hecate/ops/api/evaluation_tasks.py`:三个 GET 改造 + 只读 Task 解析 helper(缺 workspace 的非 system 提前拒绝;其余身份按存在性、删除状态与归属解析)。
- `src/hecate/ops/evaluation/tasks/service.py`:`list_tasks` 的 `workspace_id` 放宽为 Optional,仅在显式 system 列表时传 None;`list_task_runs` 保持原非 Optional 签名,API 传入已解析 Task 的真实 workspace,并增加 Run workspace 条件;`get_task`/`_get_task_or_raise` 零变化。
- 测试:新增 `tests/test_api/test_evaluation_task_access_boundary.py`(镜像既有边界套件夹具);`tests/test_api/test_evaluation_tasks_api.py`(19 例,非 system ADMIN 身份应保持绿)与 `tests/test_api` 全量回归。
- 不改报告 API、平台 Task 控制、评分查询、写接口、Runner、Runtime、前端。
