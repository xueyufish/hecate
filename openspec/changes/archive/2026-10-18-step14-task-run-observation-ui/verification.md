# 验证记录 — step14-task-run-observation-ui

## 审查结果

本次审查直接修正了以下问题，所有修改留在本 worktree，未提交或推送：

- 动作请求仅比较 Run ID，A→B→A 时旧 A 请求仍可覆盖新 A 结果。改为按每次 effect 的生命周期隔离，cleanup 后的成功/失败响应均丢弃。
- Task 路由切换缺少旧响应保护，旧任务结果或错误可覆盖当前页面。列表、详情和动作状态同时记录查询所属身份，避免旧数据在新查询下展示。
- 空列表、空动作与 unsupported 缺少手动刷新入口；后续分页变空时失去返回上一页的入口。现均保留可用导航和刷新。
- API 错误界面仅展示 message，遗漏 spec 要求的 code。列表、详情和动作错误现均展示 code/message。
- 原分页测试调用 mock 自身，没有验证页面或 URL；原实现也没有实际挂载任务列表和详情页的测试。已删除无效测试并补充真实页面覆盖。
- 原 design/tasks 声称使用 AbortController，实际没有使用。已改为真实的 effect cleanup 设计，保持全局 API client 不变。

## 自动验证（本次审查实际执行）

| 检查 | 结果 |
|---|---|
| 修复前新增回归测试 | 复现 11 个失败：动作组件 4 个，任务页面 7 个 |
| 本 change 测试 | 3 个文件，30 passed（类型/映射 9、组件 12、页面 9） |
| 前端全量 `npm test -- --reporter=dot` | 14 个文件，105 passed |
| `npm run lint` | exit 0，0 errors，2 条既有 warning |
| `npx tsc --noEmit` | exit 0，0 errors |
| `npm run build` | exit 0；列表和动态详情路由均构建成功 |
| `.venv/Scripts/python.exe scripts/dev.py check all` | exit 0；ruff check 通过，1595 个文件格式通过，mypy 检查 946 个源文件无错误 |
| `openspec validate step14-task-run-observation-ui --strict` | 通过 |
| `git diff --check` | 通过 |

实施者原测试记录的全量 93 passed 已在修复前复跑确认，但其“本 change 新增 24 例”计数不准确；修复前实际为 18 例，本次修复后为 30 例。

既有 warning 已核对来源与基线：knowledge/[id] 页的 loadDocs effect 依赖、settings/models 页的 fetchProviders effect 依赖。全量 Vitest 另有 ModelsPage 列表 key 警告，其 providers.map 返回无 key 的 Fragment；该警告在修复前的全量测试同样出现。上述两个页面与 HEAD 一致，本 change 未修改它们。

## 生产构建浏览器走查（本次审查实际执行）

使用本次 `next build` 产物，通过 `next start` 启动；仅监听本机的 Node 契约桩提供认证、Task 和 Run 动作样例数据，并代理前端资源。该后端为测试桩，未连接真实平台数据库或 Runtime。

已使用浏览器的语义定位与正常点击完成：

1. 桩账号登录后点击侧边栏 Tasks，任务列表正确显示目标、unrecorded → “未记录”、缺失创建时间 → “未记录”，单页前后翻页按钮禁用。
2. 点击目标链接进入详情，完整目标显示正确，默认选择最新 Run #2。
3. Run #2 显示 outcome_unknown、未记录 outcome、待对账标记。
4. 点击 Run #1，旧动作被替换，claimed 和 succeeded 分列显示为“已领取”和“已成功”，没有执行中语义。
5. 点击动作“刷新”，再点击“刷新任务”，当前 Run #1 选择与正确结果均保留。
6. 浏览器截图确认列表与详情布局可读、导航与按钮可用。本次未遇到实施者原记录中的点击拦截问题，未采用 DOM click 绕过交互。

分页、无 Run、空动作/unsupported 刷新、查询失败与重试、迟到响应及路由切换竞态另由上述自动测试覆盖。

## 范围及未执行项

- 改动仅涉及 web 前端及本 OpenSpec change；没有后端、Runtime、模型、迁移或发布流程变更。
- 没有控制操作按钮、自动轮询、SSE 或 Team 工作台。
- 请求清理阻止过期结果写入 UI，不中止已经发出的网络请求。
- 未执行真实平台后端联调及 CI；它们不能由契约桩走查替代。
