# Design

## Context

`RunActionsPanel` 已确立面板模式：`{runId, state}` 响应追踪、`active` 标志丢弃过期响应、`reloadTick` 手动刷新、`PanelState` 联合类型、`taskApi`/`taskErrorCode`/`taskErrorMessage` helper（`web/src/lib/task-types.ts` 经 `api` 实例请求）。后端 `GET /api/runs/{run_id}/events?cursor&limit` 返回 `{events, next_cursor, has_more}`；事件条目为 `EventEnvelope.to_dict()`：必有 `event_id`/`kind`(`event|terminal|gap`)/`source_sequence`/`occurred_at`/`received_at`/`contract_version`/`task_ref`/`run_ref`，可选 `payload_schema_ref`/`source`/`gap`(`{from_sequence,to_sequence}`)/`payload` 等。`kind=gap` 是服务端一等缺口事件，前端只做呈现、不做序号跳变推断。本设计全部为前端新增。

## Goals / Non-Goals

**Goals:**

- 事件面板：游标分页、Run 隔离、缺口呈现、错误隔离与重试，全部行为可由组件测试验证。

**Non-Goals:**

- SSE、轮询、控制命令、payload/extra 展开；不改后端、`api-client.ts`、`task-types.ts`、共享组件。

## Decisions

### D1：文件与复用边界

新增 `web/src/lib/run-event-types.ts`（`RunEventItem`/`RunEventsPage` 类型 + `runEventsApi.getEvents(runId, cursor?)`，直接 `import { api } from "@/lib/api-client"`，与 `taskApi` 同构）、`web/src/components/tasks/run-events-panel.tsx`、`web/src/__tests__/run-events-panel.test.tsx`；Task 详情页仅加 import 与 `<RunEventsPanel runId={selectedRunId} />`。不并入 `task-types.ts`：该文件属已交付动作面板切片所有，独立类型文件避免并行 change（step11 发布历史同样采用独立类型文件）冲突。

### D2：沿用面板模式并扩展分页状态

状态机沿用 `{runId, state}` + `active` 标志 + `reloadTick`，`PanelState` 扩展为 `loading | error | ready(page)`（空页也是 ready，保留 page 与游标栈，空态只影响内容呈现）。分页状态放在 `ready` 内：`page.events`、`page.has_more`、`nextCursor`（响应 `next_cursor ?? null`）、`prevCursors: (string | null)[]`（回退栈，首页元素为 `null`）。"下一页" = push 当前页游标并请求 `nextCursor`；"上一页" = pop 栈并请求栈顶游标；"刷新" = 清空栈重置为 `[null]`；Run 变化 = 整体重置（effect 依赖 `[runId, reloadTick]`，进入即 `setResponse({runId, state: loading})`，与动作面板同款）。

### D3：请求失效令牌

仅 `{runId, state}` 的 runId 匹配不足以处理"同一 Run 的旧游标请求迟到"（A 第二页慢响应覆盖刷新后的第一页）。引入单调递增 `requestSeq` ref：每次 effect/翻页/重试发起时捕获当前序号，写回前校验 `seq === requestSeq.current`，不匹配即丢弃。刷新、Run 切换、任何新请求都推进序号。

### D4：缺口呈现与字段选择

事件行按 `kind` 区分渲染：`gap` 行以醒目样式显示"缺失 `gap.from_sequence`–`gap.to_sequence`"标记（不再渲染普通字段行）；普通/terminal 行显示 `event_id`、`kind`、`source`（存在时）、`source_sequence`、`occurred_at`、`payload_schema_ref`。不渲染 `payload`/`extra`。列表语义角色沿用 `aria-live="polite"` 容器。

### D5：完成语义隔离

面板文案仅含"暂无事件""已是最后一页"等查询性表述；不读、不显示 Run/Task 生命周期字段（那是详情页既有区域的数据），从结构上排除完成性推断。

## Risks / Trade-offs

- [`next_cursor` 缺失或非字符串的防御] → 类型按 `string | null` 处理，`null` 或空字符串时禁用下一页；只有 `has_more=false` 才显示末页提示；不做格式解析（不透明字符串契约）。
- [同页内连续请求竞态（快速连点下一页）] → D3 的序号令牌天然覆盖：后发请求使先发响应全部失效；按钮在 loading 期间禁用作为第二道防线。
- [与动作面板并列导致详情页高度增长] → 面板自身不引入新布局容器，沿用页面现有堆叠；本切片不重排既有面板。

## Migration Plan

纯前端新增；回滚即移除三个新文件与页面两行装配。无数据迁移、无后端变更。

## Open Questions

（无）
