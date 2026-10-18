# Proposal

## Why

Task 详情页已能切换 Run 并查看动作记录（`RunActionsPanel`），但没有事件视图——运行期间发生了什么、哪里有缺口，只能靠后端接口手工查询。后端 `GET /api/runs/{run_id}/events`（游标分页，`next_cursor`/`has_more`）与事件契约（含一等 `gap` 缺口事件）均已交付，本切片补上前端消费，让现有运行记录可检查。这是 step14 人类观察工作台的只读补充，不改变任何后端契约。

## What Changes

- 新增 `RunEventsPanel`：Task 详情页选中 Run 后独立加载其事件页面，展示事件标识、`kind`、来源、`source_sequence`、`occurred_at` 与 `payload_schema_ref`；`kind=gap` 事件明确显示缺失序号范围（`gap.from_sequence`–`gap.to_sequence`）。
- 游标分页：`cursor` 作为不透明字符串传递，下一页使用响应 `next_cursor`，上一页按光标栈回退，保留服务端顺序；上一页/下一页/手动刷新/错误重试可用。
- 切换 Run 或手动刷新时重置分页；仅当前 Run 与当前请求的结果被采纳，旧请求的成功与错误响应一律丢弃（含 A→B→A 切换）。
- "没有事件"与"没有下一页"仅是查询结果，面板不据此推断 Run 完成、Task 成功或失败。
- 独立 API helper 复用现有 `api` 实例；面板错误被面板自身状态吸收，不影响同页动作面板。

明确不在本切片范围：SSE 订阅（`/events/stream`）、自动轮询、控制命令、payload/extra JSON 展开、后端/runtime/durable 修改、Agent 发布页面（step11 在途范围）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `task-run-observation-ui`: 新增 Run 事件时间线面板要求——游标分页、Run 切换与刷新的旧响应丢弃、缺口显式呈现、空结果不推断完成、错误隔离与重试。既有任务列表/详情/动作面板要求不变。

## Impact

- **代码**：新增 `web/src/lib/run-event-types.ts`（类型 + `runEventsApi` helper）、`web/src/components/tasks/run-events-panel.tsx`、`web/src/__tests__/run-events-panel.test.tsx`；在 `web/src/app/(dashboard)/ops-center/tasks/[id]/page.tsx` 增加 import 与组件装配，并在既有 `web/src/__tests__/tasks-page.test.tsx` 补充装配与错误隔离测试。
- **不受影响**：后端、runtime、durable、`api-client.ts`、`task-types.ts`、共享组件、Agent 详情页（step11 在途）、`execution/task_control.py`（step14 #274/#275 已合入的查询 API 范围）。
- **验证门槛**：前端 lint、类型检查、相关测试、build。
