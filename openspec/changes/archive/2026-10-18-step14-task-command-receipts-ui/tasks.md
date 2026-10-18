# Tasks

## 1. 类型与请求模块

- [x] 1.1 新增 `web/src/lib/task-command-types.ts`:`TaskCommandItem`(仅白名单字段 `command_id/kind/state/issued_at/issuer/task_ref/run_ref/expires_at/expected_revision`,`task_ref` 为 `{kind, issuer_domain, id}`，`run_ref` 为同结构或 null)、`TaskCommandsPage`(`items`/`total`)与 `taskCommandsApi.list(taskId, limit, offset)`;仅导入共享 `api` client,不修改任何既有模块。验证:`npm test` 下该模块新单测(分页查询参数拼装、不拼接单条命令路径)通过。

## 2. 命令回执面板组件

- [x] 2.1 新增 `web/src/components/tasks/task-commands-panel.tsx`:实现 loading/ready/empty/error 状态机;行渲染命令类型、状态、发起时间、发起者,可展开 `command_id`、完整 `task_ref`/`run_ref`、`expires_at`、`expected_revision`;五态固定文案映射(已请求/已确认接收/已应用/已拒绝/已过期),未知状态原样回退,`requested`/`acknowledged` 不出现"已生效"文案;缺失可选字段显式标注,`run_ref` 按 `issuer_domain/id` 等宽文本展示且不生成链接。验证:组件渲染快照/断言测试通过。
- [x] 2.2 在面板内实现 offset 分页(下一页 `offset += limit`、已访问 offset 栈回退上一页、空后续页或失败保留上一页入口、手动刷新按当前 offset)与竞态守卫(taskId keyed 内容实例 + 单调请求序号;切 Task 重建实例并重置分页、展开与序号;迟到成功/错误一律丢弃);错误消费 `detail.error.{code,message}` 并提供按当前位置重试。验证:分页与竞态单测通过。

## 3. 面板测试

- [x] 3.1 新增 `web/src/__tests__/task-commands-panel.test.tsx`(仿 `run-events-panel.test.tsx` 的 mock 方式):覆盖五种状态渲染与未知状态回退、`requested` 不因本地时间晚于 `expires_at` 被改写、命令 `applied` 不改写 Task/Run 生命周期呈现、无 Run Task 正常空态、跨 Run 命令全部展示。验证:`npm test` 通过。
- [x] 3.2 同文件补错误与竞态用例:A→B→A 迟到响应丢弃、切 Task 重置分页与展开、空后续页仍可返回上一页、后续页失败可回退、错误态展示 `code/message` 且动作/事件面板(同页 mock)状态不受影响、断言网络请求仅含 `GET /api/tasks/{task_id}/commands`(无单条命令读取、无轮询/订阅)。验证:`npm test` 通过。

## 4. 页面装配

- [x] 4.1 在 `web/src/app/(dashboard)/ops-center/tasks/[id]/page.tsx` 的"事件"区块后新增"控制命令回执"区块,以路由 `taskId` 无条件挂载面板(不进 `selectedRunId` 分支);不改动动作/事件面板与 Run 切换器。验证:详情页装配断言测试(更新 `task-views.test.tsx` 或新增装配用例)通过,面板在无 Run Task 上可见。

## 5. 门禁与收尾

- [x] 5.1 运行前端门禁并在 change 验证记录中留存实际结果:`npm run lint`、生产构建 `npm run build`、`npm test`;确认无 `@ts-ignore`/`as any`。验证:三项均 0 错误。
- [x] 5.2 运行适用共享检查与 OpenSpec 校验:`bash scripts/dev.sh check all`、`openspec validate --strict --change step14-task-command-receipts-ui`;核对功能清单是否需为本面板登记 Feature 条目,如登记则按现有 Feature ID 规则追加后缀并运行清单生成/漂移检查。验证:全部通过并在 tasks.md 勾选,验证结果写入 change 目录。

## 6. Review 修正与补充验证

- [x] 6.1 修正 A→B→A 回切保留 A 旧 offset 的问题：Task keyed 内容实例销毁旧分页/展开状态，保留请求序号 cleanup。加强既有迟到成功/错误测试，断言回切请求 offset=0 且上一页禁用。
- [x] 6.2 标签映射使用自身键判断，未知 state/kind 为对象原型属性名时仍显示原字符串；新增四组映射测试与组件渲染回归。
- [x] 6.3 补充后续页失败直接回退、Run 切换保持命令页码与展开态的真实组件装配测试。
- [x] 6.4 重新运行全量前端测试、lint、类型检查、生产构建、共享检查及严格 OpenSpec 校验，在 verification.md 留存实际 review 结果。
