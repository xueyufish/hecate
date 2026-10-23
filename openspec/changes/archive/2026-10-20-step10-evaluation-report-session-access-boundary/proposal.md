# Proposal

## Why

`GET /api/evaluation/reports/sessions` 是评测报告五个入口中最后一个未接入访问边界的:它把 `ctx.workspace_id` 直传 `EvaluationReportService.session_rollup`,无角色检查;带 `task_id` 时也不解析 Task 的存在性、删除状态与 workspace 归属。无角色身份也可读其 workspace 的会话级聚合;无 workspace 的非 system 身份把 `None` 传给过滤;system 身份传 `None` 得到空报告而非目标数据。distributions(#298)与三个聚合入口(#301)已建立报告侧边界范式,本切片复用既有助手完成最后一个入口。

## What Changes

- 只处理 `GET /api/evaluation/reports/sessions`,按是否携带 `task_id` 分两种模式:
  - 不带 `task_id`(workspace 级聚合):复用 `_require_aggregate_viewer`——无 active workspace(含 system)在查询前返回 403 `NO_WORKSPACE`;有 active workspace 但角色低于 viewer(或无角色)返回 403 `FORBIDDEN`;服务接收已验证的 `ctx.workspace_id`。
  - 带 `task_id`(资源级聚合):非 system 缺 workspace 在查询前返回同型无标识 404;否则先经 `_get_visible_task_or_404` 解析 Task(不存在/已删除/跨 workspace 共用同一无回显 404),再要求 viewer 角色(无角色 403 `FORBIDDEN`);服务使用 Task 的真实 workspace——修复 system 得到空报告的问题。
- 保持不变:会话聚合与 reconciliation 语义、按最近评分时间排序、分页、时间窗口过滤、成功响应形状、合法空结果、零 UUID workspace、只读保证。
- 其余报告入口(均已有边界)、报告服务算法、评测 Task CRUD、平台 Task、前端、Runtime 不在本切片。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-report-dashboard`: 新增"Session report access boundary"要求——无 `task_id` 的 workspace 级聚合复用聚合边界(NO_WORKSPACE/FORBIDDEN 双 403);带 `task_id` 的资源级聚合归属优先于角色(同体无回显 404)、viewer 下限、system 使用 Task 真实 workspace、查询前拒绝、零副作用与聚合语义不变。

## Impact

- 主要修改 `src/hecate/ops/api/evaluation_reports.py`(单一 API 文件,组合既有助手,不新增服务层改动);新增 `tests/test_api/test_evaluation_reports_session_boundary.py`。
- 复用同文件已有的 `_require_aggregate_viewer`、`_get_visible_task_or_404`、`_report_scope_not_found`、`_forbidden`。
- 回归:既有报告 API 测试、reconciliation 测试、aggregate 与 distributions 边界测试保持通过。
