# Proposal

## Why

`GET /api/evaluation/scores` 尚未接入读取权限边界:没有 viewer 门槛;system scope 的 `workspace_id=None` 被 service 用于等值过滤(SQL `IS NULL`),因此查询恒空;带 `task_id` 时没有先解析 Task 可见性。此前切片已完成 Task 列表/详情/运行列表边界,本切片将同一可见性契约延伸至分数查询。报告 sessions 边界仍由并行 change 负责,不据此宣称整个评测域已完成授权复核。

## What Changes

- **无 `task_id`(workspace 范围查询)**:所有身份缺 active workspace 时,在查询前返回 403 `NO_WORKSPACE`(包括 system——该模式不提供目标 workspace 选择或跨 workspace 全局分数列表);普通身份最低 `viewer`,无角色返回 403 `FORBIDDEN`;只查询已验证 workspace,不新增全局查询或 workspace 选择参数。
- **带 `task_id`(Task 范围查询)**:复用本文件既有 `_get_task_for_read` 先解析 Task(存在、未删除、归属;非 system 缺 workspace 在查询前返回恒定 404),再检查 `viewer`;缺失、已删除、跨 workspace 统一返回既有无 ID 的 Task 404;system 可跨 workspace 读取,service 接收 **Task 的真实 workspace**。
- **查询语义保持**:分页、`created_at` 升序、既有过滤参数(`target_id`/`session_id`/`metric_name`/`status`/`source`)及响应字段原样;不带 `task_id` 时继续支持 `task_id=NULL` 的人工标注与 `source=human` 查询;`session_id`/`target_id` 继续作为范围内过滤条件;disabled Task 仍可查询已有分数。
- **范围外**:service 零变化(API 保证传给 service 的 workspace 恒为具体值);Task 写接口、报告接口、人工标注接口、前端;不引入对 `evaluation_reports.py` 的依赖。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-tasks`: 新增「Evaluation score read access boundary」需求,约束分数查询的角色地板、`NO_WORKSPACE` 契约、Task 可见性对齐(不可见 Task 统一 404)与语义保持;既有评分场景在边界内保持为真,无需修改。

## Impact

- `src/hecate/ops/api/evaluation_tasks.py`:仅 `list_scores` 端点改造(复用 `_get_task_for_read` 与 `NO_WORKSPACE` 403);service、共享 helper、Task 写接口零变化。
- 测试:新增 `tests/test_api/test_evaluation_score_access_boundary.py`(镜像既有边界套件夹具);`tests/test_api/test_evaluation_tasks_api.py` 与 `tests/test_api` 全量回归(既有用例以 ADMIN 身份运行应保持绿)。
