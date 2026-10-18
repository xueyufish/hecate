# Proposal

## Why

step14 已交付 `GET /api/tasks/{task_id}/commands` 分页命令回执查询,但演进方案明确记录"命令列表尚无对应页面消费"。Task 详情页目前只有动作与事件面板,控制命令是否被接收、应用或拒绝在界面上不可见;`requested` 不等于 `applied` 的治理语义因此缺少人类可核实入口。本切片只消费既有查询 API,可与后续 Controller/Runtime 开发并行。

## What Changes

- Task 详情页新增独立"控制命令回执"面板,按 Task 查询并展示该 Task 的全部命令,不随当前选中 Run 过滤,也不依赖 Task 已有 Run。
- 面板消费 `GET /api/tasks/{task_id}/commands?limit=20&offset=0`(`items` + `total` 响应),支持分页与手动刷新。
- 准确呈现五种回执状态:`requested`→已请求、`acknowledged`→已确认接收、`applied`→已应用、`rejected`→已拒绝、`expired`→已过期;"已请求/已确认接收"不得呈现为"已生效";命令 `applied` 不得据此改写 Task/Run 状态呈现。
- 行内展示命令类型(`cancel`/`pause`/`resume`/`provide_input`)、发起时间、发起者;可展开查看 `command_id`、完整 Task/Run 引用(`issuer_domain`+`id`)、`expires_at`、`expected_revision`,缺失值显式标注。
- 仅使用列表 API,不调用带懒过期副作用的单条命令 GET;不按本地时间把 `requested` 改写为 `expired`;Run 引用按完整 `issuer_domain/id` 文本展示,不当平台 Run UUID 跳转。
- 区分加载、无命令与查询失败;展示 `detail.error.{code,message}` 并支持重试;空后续页或后续页失败时保留返回上一页;Task 切换重置分页与展开态,旧成功、旧错误及 A→B→A 迟到响应不覆盖新结果;面板失败不影响动作与事件面板;未知状态回退显示原值。
- 不加入取消、暂停、恢复、补充输入等操作按钮,不加轮询或 SSE。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `task-run-observation-ui`: 新增"Task 控制命令回执面板"要求——命令回执按 Task 查询、五态准确呈现、可展开明细、分页/错误/竞态守卫与面板错误隔离,复用该能力既有的观察语义(查询结果不推断状态、错误结构消费、迟到响应丢弃)。

## Impact

- 新增前端文件:`web/src/components/tasks/task-commands-panel.tsx`、`web/src/lib/task-command-types.ts` 及配套组件测试。
- 修改 `web/src/app/(dashboard)/ops-center/tasks/[id]/page.tsx` 装配面板(Task 级挂载,不进 Run 切换器),必要时更新对应装配测试。
- 只读消费既有 `GET /api/tasks/{task_id}/commands`;不修改后端、全局 API client(`@/lib/api-client` 仅导入)、Runtime、现有动作/事件组件。
