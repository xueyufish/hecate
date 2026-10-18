# Tasks

## 1. 类型与 API 助手

- [x] 1.1 新增 `web/src/lib/task-types.ts`：`TaskListResponse`/`TaskSummary`/`TaskDetail`/`RunSummary`/`RunActionsResponse`/`RunActionItem` 类型与 `taskApi` 助手（`list(page,pageSize,state?)`/`get(taskId)`/`getActions(runId)`，基于导出的 `api.get<T>`）；生命周期与动作状态的文案映射函数（含未知值回退原始字符串），`unrecorded` → "未记录"。验证：`web/src/lib/__tests__/task-types.test.ts` 覆盖映射表（含 claimed+succeeded 反例断言输出不含"执行中"词汇、detail.error 解析、未知值回退）全过。

## 2. 页面与组件

- [x] 2.1 新增 `web/src/components/tasks/state-badge.tsx`、`task-list-table.tsx`、`run-actions-panel.tsx`：六态呈现矩阵按 design D2（unsupported 横幅/暂无动作/错误态+刷新/unrecorded/分列/待对账 Badge）。验证：组件测试（`vi.hoisted` mock `taskApi`）覆盖迟到响应丢弃（含 A→B→A）、三态与分列呈现；分页、Run 切换由页面级测试覆盖。
- [x] 2.2 新增列表页 `web/src/app/(dashboard)/ops-center/tasks/page.tsx` 与详情页 `tasks/[id]/page.tsx`（Run 切换 + 请求生命周期 cleanup 守卫 + 手动刷新）。验证：页面级测试进入详情/切换 Run/刷新路径全过。
- [x] 2.3 `web/src/components/sidebar.tsx` 增加一条 "Tasks" 入口 Link（照既有条目样式）。验证：diff 仅含该条目及其图标 import。

## 3. 验证与收尾

- [x] 3.1 前端测试全量：`cd web && npm test`。验证：全过并报告用例数。
- [x] 3.2 lint + TypeScript 检查 + 构建：`npm run lint`、`npx tsc --noEmit`、`npm run build`（按 package.json 实际脚本名）。验证：0 错误。
- [x] 3.3 手工验证完整路径（任务列表 → 任务详情 → 切换 Run → 查看动作 → 刷新），汇总 `verification.md`：测试/lint/tsc/build 实际输出要点、手工路径结果、边界声明（无操作按钮/轮询/后端变更）。验证：tasks 全勾，实际结果记录在 verification.md 供后续 PR 引用。

## 4. 审查修正

- [x] 4.1 修复 A→B→A 旧动作响应、Task 路由切换旧响应/旧错误覆盖；列表和动作空态/unsupported 保留刷新，空后续页保留返回入口，错误态展示 code/message。验证：新增回归测试先复现 11 个失败，修复后本 change 30 个测试通过，前端全量 105 个测试通过。
- [x] 4.2 删除 mock 调用自身的无效分页测试，新增真实页面与 API URL 测试；校正 design 中 AbortController 和文案归属描述。
