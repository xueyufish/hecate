# Proposal

## Why

`GET /api/evaluation/reports/overview`、`/trends`、`/breakdowns` 三个聚合入口仍把 `ctx.workspace_id` 直接传给服务,没有 viewer 门槛:同 workspace 的无角色身份也可读全部聚合报告;无 active workspace 的身份(含 system)把 `None` 传给服务的 workspace 过滤,得到的是空报告而非拒绝。distributions(#298)已按"解析资源 → viewer 下限"建立报告侧边界;这三个入口没有目标资源参数,边界形态不同——纯粹的 workspace 成员门槛加明确的 `NO_WORKSPACE` 契约。

## What Changes

- 三个 GET 入口在服务调用前统一要求 active workspace 与最低 `viewer` 角色:
  - 有 active workspace 但角色低于 viewer(或无角色)→ 403 `FORBIDDEN`。
  - 没有 active workspace → 403 `NO_WORKSPACE`,**包括 system 身份**——这三个入口没有目标资源或 workspace 选择参数,本切片不新增 system 全局聚合能力。
  - 服务只接收已验证的 `ctx.workspace_id`,不引入零 UUID 回退;合法 workspace 的空数据继续返回正常空报告。
- 保持不变:成功响应形状、时间窗口、分桶、指标过滤、错误分数处理、overview/trends 的人工覆盖 reconciliation、breakdowns 的原始分来源统计、只读保证。
- 其余报告入口(distributions 已有边界、sessions)与报告服务算法不在本切片。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-report-dashboard`: 新增"Aggregate report access boundary"要求——overview/trends/breakdowns 的 viewer 下限、无 workspace(含 system)403 `NO_WORKSPACE`、已验证 workspace 直传服务、空报告语义与聚合/reconciliation/breakdowns 语义不变、拒绝零副作用。

## Impact

- 主要修改 `src/hecate/ops/api/evaluation_reports.py`(单一 API 文件,与 distributions 边界同文件但不同端点);新增 `tests/test_api/test_evaluation_reports_aggregate_boundary.py`。
- 复用 `ensure_workspace_role` 与 `_forbidden`;`NO_WORKSPACE` 403 码沿用 `channel/api/tasks.py` 已有约定。
- 不修改报告服务、distributions、sessions、评测 Task、平台 Task(在途 worktree 占用)、前端、Runtime 或共享 skill。
- 回归:既有报告 API 测试、reconciliation 测试与 distributions 边界测试保持通过。
