# Design

## Context

Task 详情页(`web/src/app/(dashboard)/ops-center/tasks/[id]/page.tsx`)当前装配顺序为:任务摘要 → Run 切换器(`selectedRunId`)→ 动作记录面板 → 事件面板 → 刷新按钮;动作与事件面板均以 `selectedRunId` 为键,无 Run 时页面渲染"暂无 Run,无动作/事件可查"。命令回执查询 API 已交付:`GET /api/tasks/{task_id}/commands?limit=&offset=`(`limit` 1–100 默认 20,`offset` ≥0),响应为 `{"items": [...], "total": n}`,每项为白名单字段 `command_id`、`kind`、`state`、`issued_at`、`issuer`、`task_ref`、`run_ref`(可空)、`expires_at`(可空)、`expected_revision`(可空);`task_ref`/`run_ref` 序列化为 `{kind, issuer_domain, id}`。列表读取不触发懒过期,状态原样返回(`requested/acknowledged/applied/rejected/expired`)。

既有可复用模式:类型与请求模块(`web/src/lib/run-event-types.ts`)自带类型定义与 API 封装、仅导入共享 `api` client;`run-events-panel.tsx` 用"runId 匹配 + 单调 `requestSeq`"双守卫丢弃过期响应;错误结构消费复用 `taskErrorCode/taskErrorMessage`(`@/lib/task-types`);面板测试位于 `web/src/__tests__/run-events-panel.test.tsx`。

## Goals / Non-Goals

**Goals:**

- 在 Task 级装配一个自包含的只读命令回执面板,与 Run 选择解耦;复用既有面板的状态机、竞态守卫与错误消费模式,不引入新全局依赖。
- 分页、错误、竞态、面板隔离行为全部落在面板组件内部,页面装配只增加一个挂载点。

**Non-Goals:**

- 不实现任何写操作入口(取消/暂停/恢复/补充输入)、轮询、SSE 或实时订阅。
- 不修改后端、`@/lib/api-client`、`@/lib/task-types`、动作/事件面板及 Run 切换器逻辑。
- 不做命令状态到 Task/Run 生命周期的任何联动推断;不做 `expires_at` 的本地过期计算。

## Decisions

1. **面板按 Task 挂载,独立于 Run 切换器。** 面板以 `taskId`(路由参数)为唯一数据键,在"事件"区块之后新增独立区块"控制命令回执",无条件渲染(不进 `selectedRunId` 条件分支),Task 无 Run 时照常工作。备选的"放入 Run 切换器下方并随选中 Run 过滤"被否决:命令是 Task 级事实,Run 过滤会让跨 Run 命令与无 Run Task 不可见,违反 spec。

2. **新增自包含类型模块 `web/src/lib/task-command-types.ts`。** 定义 `TaskCommandItem`、`TaskCommandsPage` 与 `taskCommandsApi.list(taskId, limit, offset)`;`TaskCommandItem` 只类型化白名单字段,`task_ref`/`run_ref` 为 `{kind, issuer_domain, id}`。错误助手经导入复用 `taskErrorCode/taskErrorMessage`,不修改 `task-types.ts`。备选"把类型并入 `task-types.ts`"被否决:保持本次变更为纯增量,避免触碰共享模块的既有导出面。

3. **offset 分页 + 已访问 offset 栈。** 与事件面板的 cursor API 不同,本 API 为 offset 分页;下一页 `offset += limit`,上一页弹出已访问 offset 栈重新请求。`total` 仅作信息展示,不用本地计算"是否有下一页"——以实际空页为准(数据可在翻页间隙变化),空后续页保留上一页入口。手动刷新按当前 offset 重新查询。

4. **Task 访问实例隔离 + 单调请求序号。** 外层面板以 `taskId` 为 React `key` 挂载内容组件，切 Task（包括 A→B→A 回切）销毁旧实例并建立新实例；页码、展开行、响应与刷新状态同步重置，避免仅按 Task identity 过滤时回切恢复旧 offset。实例内部 `requestSeq` 使旧页面、刷新、重试与 Strict Mode 重放请求失效，effect cleanup 同样使已卸载实例的在途响应失效。

5. **五态固定文案映射 + 未知值原样回退。** 状态列经映射表渲染(已请求/已确认接收/已应用/已拒绝/已过期),映射表通过 `Object.hasOwn` 查询自身键，映射表外的值（包括 `constructor`、`__proto__` 等原型属性名）直接显示原字符串;不渲染任何"已生效"替代文案。时间字段沿用 `occurredLabel` 模式(`toLocaleString`,不可解析时原样显示)。`expires_at`/`expected_revision` 缺失显示"未记录"并保留字段名标注缺失语义;`run_ref` 缺失显示"无 Run 引用",存在时以 `issuer_domain/id` 等宽文本展示,不生成链接。

6. **面板错误自吸收。** 面板内部状态机为 loading/ready/empty/error 四态,错误只改自身区块;不向页面抛出,页面其他区块不感知。重试按当前 Task 与当前 offset 重新发起。

7. **网络行为约束落在组件实现与测试断言中。** 组件只经 `taskCommandsApi.list` 取数(内部仅拼装列表 GET),不引用单条命令接口路径;测试以 fetch mock 断言请求 URL 集合,证明不发生单条命令读取、轮询或订阅连接。

## Risks / Trade-offs

- [翻页间隙新命令写入导致 offset 页漂移(漏看或重复看到某行)] → 只读观察面板按查询快照呈现,`total` 仅展示不作导航依据;空页回退上一页已覆盖最坏情况;需要权威视图时用手动刷新。
- [刷新或翻页后展开行可能不在当前页] → 展开身份按 `command_id` 保留，当前页不包含该行时不展示；只有 Task 切换销毁实例后重置。Run 切换不影响命令面板。
- [React Strict Mode 双发请求] → `requestSeq` 守卫已吸收,先发出的请求响应被丢弃,不会双渲染。

## Migration Plan

纯前端增量:新增两个文件、页面装配加一个区块、新增测试。随正常前端发布交付;回退即移除装配点(面板无持久状态、无后端契约变更),不影响既有动作/事件功能。

## Open Questions

(无——分页边界、竞态与缺失值呈现均已在 spec 固定;展示细节如 `total` 是否显示在区块标题属实现自由度。)
