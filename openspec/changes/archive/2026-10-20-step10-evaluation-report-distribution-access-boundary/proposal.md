# Proposal

## Why

`GET /api/evaluation/reports/distributions` 按具体 Run/EvaluationTask 查询分数直方图,但入口缺少 viewer 角色门槛,404 映射会回显资源 ID。服务层已有 workspace/软删除过滤,本 change 不把它描述为无过滤跨域读取:缺口是无角色身份仍可读取同 workspace 数据,无 workspace 的非 system 请求未显式提前拒绝,以及 system 的 `ctx.workspace_id=None` 直传导致指定资源被当作不存在而返回 404。此处沿用既有 Run 读边界的授权模式,在报告入口补齐最小访问契约。


## What Changes

- 只处理 `GET /api/evaluation/reports/distributions`:
  - 保持 `run_id`/`task_id` 必须且只能提供一个的既有 400(消息原文不变),以及非法 UUID 的 FastAPI 422。
  - 有效请求先解析 Run(`EvaluationRunModel`)或 **EvaluationTask** 的存在性、删除状态与 workspace 归属(归属优先于角色;缺失/已删除/跨 workspace 共用同一常量 404,不回显任何资源 ID),再要求最低 `ensure_workspace_role(ctx, WorkspaceRole.VIEWER)`;可见资源的无角色身份返回 403。
  - 参数有效但无 active workspace 的非 system 身份在资源查询前返回常量 404;显式 system 可读取指定资源,服务调用使用该资源的真实 workspace(修复 system 无法读取指定资源的问题),不引入全局聚合查询。
  - 保持直方图分箱、错误分数排除、人工覆盖 reconciliation、成功响应形状与只读保证不变。
- 其余报告入口(overview/trends/breakdowns/sessions)另行处理,本切片不动。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-report-dashboard`: 新增"Distributions report access boundary"要求——Run/Task 两种 scope 的解析顺序(归属优先于角色)、viewer 下限、同体无回显 404、system 真实 workspace 取数、无 workspace 非 system 拒绝、400/422 与成功语义保持、拒绝零副作用。

## Impact

- 主要修改 `src/hecate/ops/api/evaluation_reports.py`(单一 API 文件);新增 `tests/test_api/test_evaluation_reports_distribution_boundary.py`。
- 复用 `evaluation.py` 的 `_get_visible_run_or_404`/`_forbidden` 与 `ensure_workspace_role`;Task scope 在本文件新增小解析器(无既有 helper 可复用)。在本入口将复用 Run helper 的 404 转为与 Task 相同的报告常量,不改变原 Run API;`_run` 同步映射服务再次查询的 NotFound,保证预检查后资源失效也不回显标识。
- 不修改两个在途 worktree 占用的文件(`evaluation.py` 主体、`dataset_service.py`、`channel/api/tasks.py`、`execution/task_control.py`)、共享 skill、前端或报告计算算法(服务层 `reports/service.py` 不动)。
- 回归:既有报告 API 测试、`test_reports_reconciliation.py` 与 Run 读边界测试保持通过。
