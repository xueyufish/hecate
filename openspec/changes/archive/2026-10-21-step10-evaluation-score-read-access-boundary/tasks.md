# Tasks

## 1. 读取边界实现(`src/hecate/ops/api/evaluation_tasks.py`)

- [x] 1.1 复用 `evaluation.py` 的 `_no_workspace()`(403,code `NO_WORKSPACE`,恒定消息,envelope 同形);`GET /scores` 无 `task_id` 路径:`ctx.workspace_id is None → _no_workspace()` 先于角色,再 `ensure_workspace_role(ctx, VIEWER)` → `_forbidden`,以 `ctx.workspace_id` 调用 `svc.list_scores`。验证:缺 workspace 身份(含 system)查询前拒绝,不再产生 `IS NULL` 空结果。
- [x] 1.2 `GET /scores` 带 `task_id` 路径:`_get_task_for_read(ctx, task_id, db)` → 角色 → `svc.list_scores(workspace_id=task.workspace_id, task_id=task_id, ...)`;不可见 Task 返回既有 `_not_found()`;service 零变化。验证:不可见 Task 三态(缺失/软删/跨 workspace)同体 404 非 200 空列表;system 带 task_id 返回真实结果。
- [x] 1.3 共享 helper(`_get_task_for_read`/`_not_found`/`_forbidden`)、Task 写接口、`svc.list_scores` 签名与查询零变化。验证:`git diff` 范围核对。

## 2. 边界测试(新增 `tests/test_api/test_evaluation_score_access_boundary.py`)

- [x] 2.1 使用既有 db_session/client 夹具和直接创建的 Task/Score 行,覆盖 spec 全部场景:双 workspace 隔离(total)、`NO_WORKSPACE` 含 system 且查询前拒绝、viewer 正常读取/无角色 403、不可见 Task 三态同体 404 零 ID、system 带 task_id 跨 workspace 真实结果、人工标注(`task_id=NULL` 行 + `source=human`)兼容、disabled Task 可读、分页/`metric_name`/`status`/升序保持、拒绝只读(表直查全列快照)。验证:新文件全部通过。
- [x] 2.2 404/403 响应体零资源 ID 回显断言。验证:测试显式覆盖。

## 3. 既有回归(`tests/test_api/test_evaluation_tasks_api.py` 等)

- [x] 3.1 排查既有评分相关用例(ADMIN 身份应保持绿;system 行为断言按修复后语义修正属预期)。验证:`python -m pytest tests/test_api/test_evaluation_tasks_api.py tests/test_api/test_evaluation_score_access_boundary.py tests/test_api/test_evaluation_task_access_boundary.py -q` 全绿。

## 4. 回归与变更验证

- [x] 4.1 运行 `python -m pytest tests/test_api -q -n 4` 全量。验证:0 failed。
- [x] 4.2 运行 `openspec validate step10-evaluation-score-read-access-boundary --strict` 与共享检查(`scripts/dev.py check all`:共享 Ruff/format 范围,mypy 于 `src/ packages/ scripts/dev.py`;记录实际结果)。验证:全部通过,`git diff` 不含 service、报告接口、Task 写接口、前端变更;实际结果写入 verification.md。

## 5. 审查补充

- [x] 5.1 修正规格中 NO_WORKSPACE 场景的 task_id 条件、system 角色豁免与 Task 范围 viewer 门槛,校正文档的实际 helper 复用方式和范围声明。
- [x] 5.2 补齐完整错误 envelope、角色/可见性顺序、查询前拒绝、异常 workspace/已删除分数排除、人工审计字段、精确排序/分页/过滤及 Task/Score 全列只读快照;移除无用 registry 测试代码。
- [x] 5.3 执行受影响 API/service 回归、共享 check all、OpenSpec strict 和差异范围检查,实际结果写入 verification.md。
