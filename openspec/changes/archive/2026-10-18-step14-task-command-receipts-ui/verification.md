# Verification Record

## Review 范围与修正

基点：`origin/main` `e7960d42`；分支 `feat/step14-task-command-receipts-ui`。本次 review 直接检查并修正用户指定 worktree 的未提交实现；没有提交、推送或归档。

- 修正 A→B→A 回切沿用 A 第二页的问题。原实现只按 `position.taskId` 决定是否回退首页，切到 B 后并未清除 A 的位置；改用 `taskId` keyed 内容实例，每次 Task 访问重建分页、展开和响应状态，cleanup 使旧请求失效。
- 修正未知 state/kind 的对象原型属性名回退：映射表通过 `Object.hasOwn` 查询，避免 `constructor` 返回函数、`__proto__` 返回对象导致内容缺失甚至 React 渲染失败。
- 加强迟到成功/错误测试，明确断言回切请求 offset=0；补充四组原型名称映射、组件渲染、失败后续页直接回退、切换 Run 保持命令分页与展开态的测试。新增装配测试使用命令面板内的局部按钮定位，避免与事件面板的分页按钮重名。
- 同步 design/tasks/specs，纠正 Task 引用可空、过期时间方向和刷新重置展开等不符合实际契约或实现的描述。

## 实际验证结果

| 检查 | 命令 | Review 结果 |
|---|---|---|
| 修复前回归 | `npx vitest run src/__tests__/task-commands-panel.test.tsx src/lib/__tests__/task-command-types.test.ts --reporter=dot` | 7 failed / 19 passed，复现回切 offset=20 与原型属性回退问题；含预期的 React 未捕获渲染异常 |
| 修复后回归 | 同上 | 26 passed / 2 files，0 未捕获异常 |
| 装配及版本面板复核 | `npx vitest run src/__tests__/task-detail-commands-panel.test.tsx src/__tests__/version-panel.test.tsx --reporter=dot` | 9 passed / 2 files |
| 前端全量 | `npm test -- --reporter=dot`（web/） | 最终 179 passed / 19 files；0 失败；ModelsPage 存量 React key warning |
| Lint | `npm run lint`（web/） | 退出码 0；两个未改动文件的 useEffect 依赖 warning：knowledge/[id] 的 loadDocs、settings/models 的 fetchProviders |
| 生产构建 | `npm run build`（web/） | 退出码 0；28 个静态页面生成完成，含 lint 与 TypeScript 校验 |
| TypeScript | `npx tsc --noEmit`（web/） | 退出码 0，无类型错误 |
| 共享检查 | `.venv/Scripts/python.exe scripts/dev.py check all` | 退出码 0；ruff check、1613 files format check、mypy 959 source files 全部通过 |
| 功能清单治理 | `.venv/Scripts/python.exe scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings（显式治理债务） |
| OpenSpec | `openspec validate step14-task-command-receipts-ui --strict` | valid；review 文档更新后再次校验 |
| 补丁卫生 | `git diff --check`；新增/修改代码类型抑制检查 | 通过，无 `as any` / `@ts-ignore` |

第一次全量运行有两处失败：新增装配测试的“下一页”定位歧义已修正；未改动的 `version-panel.test.tsx` 初始徽章断言可在异步版本列表完成前通过，紧随其后的同步空态断言因此偶发失败。已核对该测试及 `components/agent/version-panel.tsx` 相对 HEAD 均无改动；代码中的 Badge 在 status=null 时就渲染“未提交”，加载结束才渲染空态，确认为测试等待条件的竞态。单独复核 9 用例与最终默认配置全量 179 用例均通过；本 change 未扩展修改无关版本面板文件。

## 浏览器验证

使用本次生产构建与仅绑定 127.0.0.1 的临时 HTTP 夹具代理（5781→5782），通过真实页面登录表单输入虚构账号；未连接真实后端或真实用户数据。

- 五种状态和未知 `constructor`、`__proto__` 原样显示，页面正常渲染；Task 摘要仍为服务端 `running`。
- `requested` 携带过去的 `expires_at` 仍显示“已请求”；明细展示缺失 Run 引用、缺失可选值、`expected_revision=0` 和完整 `external-runner/opaque-run-1` 文本，未生成 Run 跳转链接。
- 切换 Run 后保持已展开命令明细，代理没有新增命令查询。
- 下一页返回 500 时显示结构化 code/message 并保留“上一页”；重试仍查询 offset=20；重试返回空页后可回到 offset=0，重新显示 20 条记录。
- 无 Run Task 仍展示命令面板与回执，动作/事件区域保持“无 Run”提示。
- 代理记录的命令请求全部为列表 GET：offset 0、20、20、0，以及无 Run Task 的 offset 0；未出现单条命令 GET。
- 浏览器控制台无 error/warn；临时标签页及两个服务器进程已关闭。

## 范围限制

没有修改后端、全局 API client、Runtime 或动作/事件组件。CI、真实后端集成验证及 PR review 未在此本地检查中执行。未新增独立功能清单条目，保持现有观察 UI 切片的登记范围，功能清单检查确认无错误。交互命令按钮、轮询、SSE 均不在本 change 范围。
