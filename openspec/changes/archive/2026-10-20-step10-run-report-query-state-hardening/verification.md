# Verification Record

## Review 范围与修正

基点：`origin/main` `e8e52e1f`；分支 `feat/step10-run-report-query-state-hardening`。直接 review 用户指定 worktree 的未提交改动；没有提交、推送、归档或修改其他 worktree。

- 修正未选择 Run 时的误导性空态：Run 列表加载中、失败或没有 completed Run 时，Low Scores 不再显示“No scores recorded for this run.”，而是“Select a run to see scores.”。只有选中 Run 的对应查询成功且为空时，才显示无评分。
- 保留 GLM 实现的 Run keyed 内容实例及两路独立请求序号；补测同一 Run 时间窗口重查的迟到成功/错误、单侧重试不使另一侧在途请求失效，以及切换 Run 后评分重试目标正确。
- Run 切换测试改为先打开真实选择器再选择选项，等待选择器当前值呈现，不依赖 jsdom 未加载 Tailwind 时隐藏菜单仍可被查询的行为。
- 同步 design/specs/tasks：empty 为 ready 的空数据分支，分布与评分使用独立序号，未知 source 使用中性 outline 徽标；补齐未选择与成功空态的边界。

## 实际验证结果

| 检查 | 命令 | Review 结果 |
|---|---|---|
| 修复前回归 | `npx vitest run src/components/evaluation/run-report-view.test.tsx --reporter=dot`（web/） | 5 failed / 17 passed；失败分别覆盖列表加载、403/404/500 错误、无可选 Run 时仍误报无评分 |
| 修复后组件回归 | `npx vitest run src/components/evaluation/run-report-view.test.tsx src/components/evaluation/evaluation-views.test.tsx --reporter=dot`（web/） | 31 passed / 2 files，其中新文件 23 用例，既有组件测试 8 用例保持通过 |
| 完整前端测试 | `npm test -- --reporter=dot`（web/） | 202 passed / 20 files，0 失败；ModelsPage 有存量 React key warning |
| Lint | `npm run lint`（web/） | 退出码 0；仅 knowledge/[id] 的 loadDocs、settings/models 的 fetchProviders 两个既有 useEffect 依赖 warning |
| 生产构建 | `npm run build`（web/） | 退出码 0；28 个静态页面生成完成，包含 lint 与类型检查 |
| TypeScript | `npx tsc --noEmit`（web/） | 退出码 0，无类型错误 |
| 共享检查 | `.venv/Scripts/python.exe scripts/dev.py check all` | 退出码 0；ruff check、1614 files format check、mypy 959 source files 全部通过 |
| 功能清单 | `.venv/Scripts/python.exe scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings（显式治理债务） |
| OpenSpec | `openspec validate step10-run-report-query-state-hardening --strict` | valid；review 文档更新后再次校验 |
| 补丁卫生 | `git diff --check`；修改代码类型抑制扫描 | 通过，无 as any / @ts-ignore / @ts-nocheck |

两次中间回归因 review 新增的选择器辅助函数最初假定 trigger 显示短标签，且没有等待当前值完成注册而失败，已修正为等待当前值出现后打开菜单；最终相关 31 用例与完整 202 用例均通过。未改动既有 evaluation-views.test.tsx、全局 API client、后端或其他产品视图。

## 验收覆盖

- 首次加载、成功无 completed Run、Run 列表 FORBIDDEN/NOT_FOUND/INTERNAL_ERROR 与重试，均不会被误报为某 Run 无评分。
- 选中 Run 的真实成功空分布/空评分仍有明确空态。
- 分布失败而评分成功、评分失败而分布成功；分别重试不重查另一侧，也不作废另一侧在途响应。
- 切 Run 隐藏旧数据与展开行；旧成功、旧错误、A→B→A 迟到响应丢弃。
- 同一 Run 的 startDate/endDate 重查仍只使用原有 Run 参数，新结果不受旧成功/错误覆盖；不重查列表、不扩展时间过滤。
- 分布与评分重试都针对当前选中的 Run。
- 未知 source（含 constructor / __proto__）按原值和中性 outline 徽标呈现。

## 浏览器验证

使用本次生产构建（127.0.0.1:6782）及临时 HTTP 夹具代理（127.0.0.1:6781），通过实际登录表单输入虚构账号，不连接真实后端或真实账号。

- Run Report 实际列表 GET 返回 403 时显示 FORBIDDEN 与原始 message；分布和低分区域只显示选择 Run 提示，无空评分误报。
- 点击列表重试后选中 Run A：分布 404 显示 NOT_FOUND 与重试，评分正常显示 20%；分布重试后图表恢复，评分请求没有重复。
- 点击评分展开显示 reasoning；未知 constructor source 原样显示 outline 徽标。
- 先打开实际下拉菜单，再选择 Run B：分布显示 metric-b，评分 503 显示 TEMPORARY，旧 Run A 的展开明细不再展示。
- 单独重试 Run B 的评分后显示 70%；代理记录该重试 URL 仍为 B 的 scores，分布不重查。
- 两个列表请求来自页面同时挂载的 Run Report 与既有 Compare；Run Report 的手动重试另发起一次列表 GET。全部评分请求保留 page_size=100，分布请求只有 run_id，没有扩展时间过滤。
- 浏览器控制台无 error/warn；临时标签页及两个服务器已关闭。

## 未测范围与边界

未运行真实后端集成或 CI；未同步主 specs、创建 PR 或归档。保留既有 completed 过滤、value 升序、前 20 条与时间参数仅触发重查的语义。分页、Compare/Online/Annotation 等其他视图、Overview 失败处理、后端与 Runtime 不在本次 change 范围。功能清单未新增条目，本次仍属于既有 evaluation-report-dashboard 能力的修正。
