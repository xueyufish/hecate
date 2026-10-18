# Design

## Context

已核实的设施基线（`origin/main` @ `42035439`）：

- **API 就位**：`GET /api/tasks`（`{items,total}`，分页参数）、`GET /api/tasks/{task_id}`（goal/`lifecycle_state`/created_at/`runs[]`——每 run 含 run_id/attempt_no/backend_ref/projection）、`GET /api/runs/{run_id}/actions`（白名单动作 + `supported`/`ledger_source`）。无 durable 记录的 Task 返回 `lifecycle_state: "unrecorded"`（既有常量 `UNRECORDED_LIFECYCLE`）。错误经 `_error()` 包装在 `detail.error.{code,message,details}` 下。
- **前端先例**：`(dashboard)/ops-center/{agents,conversations,evaluation}` 页面（"use client" + hooks + `@/components/ui/*`）；`lib/api-client.ts` 导出 `api` 单例（get/post 带认证刷新）与命名空间 API 对象（`evaluationApi` 先例）；vitest + testing-library（`lib/__tests__` 与 `evaluation-views.test.tsx` 的 `vi.hoisted` API mock 先例）；`sidebar.tsx` Link 条目。
- **两条在途线**（step6-dispatch、step11-publication-receipt）不触及 `web/`。

## Goals / Non-Goals

**Goals:** 列表/详情两个页面及其组件 + 类型文件 + 测试 + 一条侧边栏入口；六态呈现与竞态守卫钉为行为契约。

**Non-Goals:** 不改后端、不改 `lib/api-client.ts`（新文件基于导出的 `api` 单例）、不做分页组件抽象（简单上一页/下一页即可）、不做自动轮询/SSE/操作按钮/Team/完整工作台。

## Decisions

### D1: 文件布局与 API 访问方式

- `web/src/lib/task-types.ts`：响应类型（`TaskListResponse`/`TaskDetail`/`RunSummary`/`RunActionsResponse`/`RunActionItem`）+ `taskApi` 助手（`list/get/getActions`，基于导出的 `api.get<T>`）——不编辑 `api-client.ts`，认证刷新逻辑经单例复用；
- 页面：`web/src/app/(dashboard)/ops-center/tasks/page.tsx`（列表）、`tasks/[id]/page.tsx`（详情，Next.js App Router 动态段）；
- 组件：`web/src/components/tasks/task-list-table.tsx`、`run-actions-panel.tsx`、`state-badge.tsx`（状态 Badge 组件）；
- 测试：`web/src/lib/__tests__/task-types.test.ts`（类型守卫/映射函数）+ `web/src/components/tasks/*.test.tsx`（动作组件行为，`vi.hoisted` mock `taskApi`）；`web/src/__tests__/tasks-page.test.tsx` 通过 mock 底层 `api.get` 覆盖真实列表/详情页面与 API URL；
- `sidebar.tsx`：一条 "Tasks" Link（`/ops-center/tasks`）。

备选——把 `taskApi` 加进 `api-client.ts`——被否决：提案边界明确不改该文件；`evaluationApi` 也在其中，但新增命名空间放独立文件同样可复用单例，且避免与两条在途线的 rebase 面接触。

### D2: 六态呈现矩阵（状态文案映射归属 `task-types.ts`）

| 数据事实 | 呈现 |
|---|---|
| 请求中 | 骨架/加载中 |
| 列表 items 为空 | "暂无任务" |
| 详情 runs 为空 | "暂无 Run" |
| 动作 `supported=false` | "不支持动作查询"横幅 |
| 动作 `supported=true` 且空 | "暂无动作" |
| 动作请求异常 | 错误态（`detail.error.code/message`）+ 手动刷新按钮 |
| `lifecycle_state=unrecorded` | "未记录"（列表与详情一致，不推断完成） |

动作行：`state` 列（恢复四态文案）与 `outcome` 列（succeeded/failed/unknown/未记录）**分列**；`claimed + succeeded` 组合整体呈现"已领取（已成功）"，不使用执行中语义；`pending_reconciliation=true` 显示待对账 Badge。文案映射集中在 `task-types.ts`，由 `state-badge.tsx` 消费，测试钉住映射表（`claimed+succeeded` 不得输出"running/执行中"词汇）。

### D3: 竞态守卫的最小实现

详情页持有 `selectedRunId` state。列表、详情、动作查询均在 `useEffect` 中发起，每次 effect 拥有独立的 active 标志；cleanup 后的成功和失败响应均丢弃。清理标志绑定请求生命周期，而非只比较 Run ID，因此 A→B→A、Strict Mode effect replay 和页面卸载不会接受旧响应。现有 `api.get` 没有 AbortSignal 参数，本 change 不修改全局 API client，也不声称中止了网络请求。

查询状态同时记录所属 page/taskId/runId；所属查询变化时立即呈现加载态，避免把旧结果显示在新的路由或 Run 下。手动刷新通过 reloadTick 触发新的 effect；空列表、空动作和 unsupported 状态保留刷新入口。列表后续页变空时仍保留返回上一页的入口。

### D4: 测试分层

1. 动作组件测试（mock `taskApi`）：迟到响应和 A→B→A 丢弃、unsupported/暂无动作/错误码与刷新、claimed+succeeded 分列及待对账；页面测试（mock 底层 `api.get`）：真实分页 URL、详情链接、Run 切换、刷新、路由切换旧响应/旧错误丢弃、空后续页返回、无 Run、错误码与重试；
2. `task-types.test.ts`：状态文案映射表（含反例：映射输出不含"执行中"）、`detail.error` 解析；
3. 既有套件回归（vitest 全量）+ lint + `tsc --noEmit` + `next build`。

### D5: 分层 spec-driven

新增用户可见行为面（新页面/新能力）；新能力 `task-run-observation-ui` 承载三条要求（列表与详情消费、竞态守卫与三态、状态分列不跨语义），场景对应验收清单。

## Risks / Trade-offs

- [与 api-client.ts 内既有任务类型潜在重复] → 检索确认 api-client 现无 task 类型；如后续合并出现重复，收敛方向是 task-types.ts 成为唯一归属。
- [Next.js 动态段/客户端钩子的框架行为差异] → 照 evaluation 页面的既有 App Router 模式；构建与测试实跑验证。
- [竞态守卫被 React 严格模式双调用干扰] → effect cleanup 按每次请求隔离，成功与失败均受保护；测试以"最终渲染 = 选中 Run"断言。
- [文案映射遗漏后端新增状态] → 映射函数对未知值回退显示原始字符串（不吞状态），测试钉住该回退。

## Migration Plan

纯前端增量；回退 = revert 单 commit（侧边栏条目一并消失，无数据/兼容影响）。

## Open Questions

（无——布局、呈现矩阵、竞态实现、测试分层均已定。）
