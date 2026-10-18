# Proposal

## Why

`GET /api/runs/{run_id}/actions`（#274）与既有的 `GET /api/tasks`、`GET /api/tasks/{task_id}` 已提供完整的只读查询能力，但没有前端入口消费它们——人类观察仍只能调 API。本片交付最小观察视图：任务列表、任务详情（含 Run 切换）与动作记录展示，把刚合入的查询能力变成可用入口。与演进方案 step14"最小观察视图先行"的方向一致；与在途 step6-dispatch/step11-publication-receipt 两条线零文件重叠（均不触及 `web/`）。

## What Changes

1. **任务列表页 `/ops-center/tasks`**：消费 `GET /api/tasks`（分页），展示目标摘要、生命周期状态、创建时间，支持翻页与进入详情；侧边栏新增入口。
2. **任务详情页 `/ops-center/tasks/[id]`**：消费 `GET /api/tasks/{task_id}`（完整目标、生命周期状态、Run 列表）与 `GET /api/runs/{run_id}/actions`（动作记录）；Run 切换查看；手动刷新。
3. **状态呈现准确性**：区分加载中/无 Task/无 Run/无动作/不支持/查询失败六种呈现；`supported=false` 显示"不支持动作查询"而非"暂无动作"；动作的 `state` 与 `outcome` 分列展示（`claimed + succeeded` 不得显示为"仍在执行"）；`unrecorded` 生命周期显式显示"未记录"，不推断完成。
4. **竞态与错误处理**：快速切换 Run 时仅接受当前选中 Run 的动作响应（按请求生命周期守卫，包含 A→B→A 的旧请求，丢弃过期响应）；消费 `detail.error.{code,message}` 错误结构。

**边界**：纯前端只读消费；不扩展后端 API、不改模型/迁移/Runtime/发布流程、不修改全局 `lib/api-client.ts`（新文件基于导出的 `api` 单例构建）；不做暂停/取消/审批/自动轮询/SSE/Team/完整工作台。

## Capabilities

### New Capabilities

- `task-run-observation-ui`: Task/Run 只读观察前端的行为契约——列表与详情消费、Run 切换与竞态守卫、动作记录的三态与状态分列呈现、`unrecorded` 不推断、错误结构消费。

### Modified Capabilities

（无——零后端行为变更。）

## Impact

- **新增**：`web/src/app/(dashboard)/ops-center/tasks/page.tsx`、`web/src/app/(dashboard)/ops-center/tasks/[id]/page.tsx`、`web/src/components/tasks/`（列表表格与动作面板组件）、`web/src/lib/task-types.ts`（类型 + 基于 `api` 单例的 `taskApi` 助手）、对应 vitest 测试。
- **修改**：`web/src/components/sidebar.tsx`（一条入口 Link）。
- **验证**：前端测试、lint、TypeScript 检查、构建；手工验证完整路径。
- **不涉及**：后端任何文件、`lib/api-client.ts`、模型/迁移、Runtime、feature-catalog。
