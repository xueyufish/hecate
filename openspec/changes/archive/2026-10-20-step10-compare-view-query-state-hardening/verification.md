# Verification Record

## Review 范围与修正

基点：`origin/main` `5b57c29e`；分支 `feat/step10-compare-view-query-state-hardening`。检查并修正用户指定 worktree 的未提交实现，没有提交、推送、归档或修改其他 worktree。

- 修正同值重选导致 comparing 卡住：共享 Select 对当前选项重选仍会触发 onValueChange；原处理总会把 submitSeq 清零，Run 对未变化却触发旧 effect cleanup，使当前比较/重试响应失效且没有新请求替代。现在 baseline/candidate 只有 ID 实际变化才重置提交计数并更换 Run 对实例。
- 列表错误改用组件内已有解析器，保留字符串 detail 与 API client 顶层 error.code/message；未知错误的兜底文案按列表/比较区指定。未改全局 API client 或 task-types。
- 强化回归：同值重选的成功/错误与重试、旧 ready/error 随任一选择变化立即清除、A/B→A/C→A/B 迟到错误、同对新旧请求竞争、(A,B)/(B,A) 有序语义。
- 测试先打开实际下拉菜单再点击选项，不依赖 jsdom 未加载 CSS 时隐藏选项仍可被查询的行为。初版 review 辅助函数 closest 命中了选项自身的 relative class，已修正为从其父节点寻找选择器容器；该测试定位问题不作为产品缺陷计数。
- 同步 design/tasks/specs：真实实现使用 keyed 内容实例与请求闭包固定 Run 对，并非 ready 状态额外保存请求 ID；明确同值重选不重置、两类错误信封消费及比较区加载态行为。

## 实际验证结果

| 检查 | 命令 | Review 结果 |
|---|---|---|
| 修复前回归 | `npx vitest run src/components/evaluation/compare-view.test.tsx --reporter=dot`（web/） | 辅助函数修正后 7 failed / 20 passed：五个同值重选/重试用例、两个列表错误形状用例复现失败 |
| 修复后组件回归 | `npx vitest run src/components/evaluation/compare-view.test.tsx src/components/evaluation/evaluation-views.test.tsx --reporter=dot`（web/） | 35 passed / 2 files；独立 Compare 测试 27 用例、既有组件测试 8 用例 |
| 完整前端测试 | `npm test -- --reporter=dot`（web/） | 229 passed / 21 files；0 失败；ModelsPage 有存量 React key warning |
| Lint | `npm run lint`（web/） | 退出码 0；仅 knowledge/[id] 的 loadDocs、settings/models 的 fetchProviders 两个未改动文件的 useEffect 依赖 warning |
| 生产构建 | `npm run build`（web/） | 退出码 0；28 个静态页面生成完成，包含 lint 与类型检查 |
| TypeScript | `npx tsc --noEmit`（web/） | 退出码 0，无类型错误 |
| 共享检查 | `.venv/Scripts/python.exe scripts/dev.py check all` | 退出码 0；ruff check、1614 files format check、mypy 959 source files 全部通过 |
| 功能清单 | `.venv/Scripts/python.exe scripts/feature_inventory.py check` | 413 entries，0 errors，189 warnings（显式治理债务） |
| OpenSpec | `openspec validate step10-compare-view-query-state-hardening --strict` | valid；review 文档更新后再次校验 |
| 补丁卫生 | `git diff --check`；修改代码类型抑制扫描 | 通过，无 as any / @ts-ignore / @ts-nocheck |

保留 GLM 对既有 evaluation-views.test.tsx 的两处 getByText→findByText 等待修正，成功路径断言未放宽。新增测试与已通过的既有比较用例共同验证逐指标 delta、配对 deltas、drift 与错误重试；计算方式、API 参数与分页范围保持不变。

## 浏览器验证

使用本次生产构建（127.0.0.1:7782）与本地模拟接口代理（127.0.0.1:7781），实际登录表单仅输入虚构账号，不连接真实后端。

- Run 列表返回非 JSON 的 502 时，Compare 页面显示 API client 的 `[UNKNOWN] Bad Gateway`，列表重试后恢复选择器。
- 手动选择 (A,B) 并提交；模拟接口暂不返回结果。在真实下拉菜单中重选当前 Candidate B 后，释放原请求，页面成功显示 fixture-1，既不重发请求也不卡在比较中。
- 同对再次提交返回 422 字符串 detail 时，显示完整比较失败原因，旧成功结果消失；重试仍按 (A,B) 并恢复 fixture-3。
- Candidate 改成 C 后，旧结果立即清除且保持未提交；再次手动提交返回 404 时显示 `[NOT_FOUND] Run not found`，重试按 (A,C) 并显示 fixture-5。
- 同值重选与更换选择均没有自动提交额外比较；操作顺序为 (A,B) 三次、(A,C) 两次。浏览器控制台无 error/warn；临时验证标签页已关闭。

## 未测范围与边界

未运行真实后端集成或 CI；未同步主 specs、创建 PR 或归档。未改动 Run Report、Overview/Online/Annotation、后端、全局 API client、Runtime、共享 skill 或功能清单条目。本地验证不宣称真实权限服务或生产部署通过。
