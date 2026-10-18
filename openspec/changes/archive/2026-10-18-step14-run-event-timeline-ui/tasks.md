# Tasks

## 1. 类型与 API helper

- [x] 1.1 新建 `web/src/lib/run-event-types.ts`：`RunEventItem`（含 `kind` 判别联合：event/terminal 行字段与 gap 范围字段）、`RunEventsPage`（`events`/`next_cursor`/`has_more`）与 `runEventsApi.getEvents(runId, cursor?)`，复用 `api` 实例；以类型被面板与测试引用、lint 通过为证
- [x] 1.2 对照 `EventEnvelope.to_dict()` 实际键名（`event_id`/`kind`/`source`/`source_sequence`/`occurred_at`/`payload_schema_ref`/`gap.from_sequence`/`gap.to_sequence`）核对类型定义，必要字段缺失或多余时修正

## 2. 面板组件

- [x] 2.1 新建 `web/src/components/tasks/run-events-panel.tsx`：按 design D2 实现状态机与分页（首页加载、下一页用 `next_cursor`、上一页回退栈、`has_more=false` 禁用下一页、刷新重置、Run 切换重置），以"翻页顺序保持""刷新回第一页"的测试通过为证
- [x] 2.2 按 design D3 实现请求失效令牌（`requestSeq`）：切 Run（A→B→A）、刷新、快速翻页时旧成功与旧错误响应均被丢弃；以三条竞态测试通过为证
- [x] 2.3 按 design D4/D5 实现渲染：`kind=gap` 行显示缺失范围标记、普通/terminal 行显示六字段、空态与末页仅查询性文案、错误态含重试（重试带当前游标不重置）；以缺口、空态、末页、错误重试测试通过为证

## 3. 页面装配

- [x] 3.1 在 `web/src/app/(dashboard)/ops-center/tasks/[id]/page.tsx` 增加 import 与 `<RunEventsPanel runId={selectedRunId} />` 装配，不改既有面板；以页面渲染 smoke（既有测试套件不回归）为证

## 4. 测试

- [x] 4.1 新建 `web/src/__tests__/run-events-panel.test.tsx`：mock `runEventsApi`，覆盖 loading/empty/error/retry/ready 五态、翻页与回退、刷新重置、切 Run 重置、A→B→A 旧响应丢弃、缺口范围显示、无完成性表述断言
- [x] 4.2 事件面板错误不影响动作面板：同页装配下事件接口失败、动作接口正常的组合断言（沿用既有动作面板测试的装配方式）

## 5. 验证与收尾

- [x] 5.1 运行前端 lint 与类型检查（`npm run lint` / `npx tsc --noEmit` 或仓库对应脚本）达到 0 错误
- [x] 5.2 运行相关测试（新增面板测试 + 既有 `run-actions-panel`/页面测试）全部通过
- [x] 5.3 运行 `npm run build` 成功；`openspec validate step14-run-event-timeline-ui --strict` 通过，并逐条核对 delta spec 场景均有对应测试


## 6. 审查修正

- [x] 6.1 保留空页的游标与历史栈，修正空末页无法回退、空中间页无法继续的问题；缺失 next_cursor 时禁用下一页
- [x] 6.2 将 RunEventItem 对齐为 kind 判别联合，补齐 received_at 必需字段；补充真实游标编码、三页回退、第二页 A→B→A 成功/错误竞态与页面装配测试
- [x] 6.3 重新运行前端完整测试、lint、类型检查、build、共享静态检查及 OpenSpec strict validation，记录实际结果


## 审查验证结果

- 修正前，新增分页边界测试复现 3 项失败：空页丢失导航与末页提示，以及缺失 next_cursor 时按钮仍可点击。
- `npm run test`：15 个文件、122 项测试通过；最终事件面板测试单独复跑 16 项通过。
- `npm run lint`：0 errors；存在两条与 origin/main 一致的 Hook dependency warnings，分别来自 knowledge/[id]/page.tsx 和 settings/models/page.tsx，均不在本 change 修改范围。
- `npx tsc --noEmit`：通过。
- `npm run build`：成功，Task 详情路由构建正常；仅上述相同 lint warnings。
- `.venv/Scripts/python.exe scripts/dev.py check all`：ruff check 通过、1601 files 格式检查通过、mypy 950 source files 无错误。
- `openspec validate step14-run-event-timeline-ui --strict`：通过。
- `git diff --check`：通过。未提交、未推送。
