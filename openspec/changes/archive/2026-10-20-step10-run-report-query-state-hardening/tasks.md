# Tasks

## 1. RunReportView 状态机改造

- [x] 1.1 改造 `web/src/components/evaluation/run-report-view.tsx`:Run 列表、评分分布、低分列表各自持有 loading/empty/error/ready 状态;错误消费 `detail.error.{code,message}`(复用导入 `taskErrorCode/taskErrorMessage`)并提供重试,失败不得渲染"No scores recorded"文案;分布与评分分别独立 try/catch,任一路失败不清除另一路结果。验证:组件断言测试通过。
- [x] 1.2 以 `runId` 为 React `key` 拆出报表内容组件(切 Run 整体重置:分布/评分回加载态、展开行收起);实例内用单调请求序号守卫重试与 `startDate/endDate` 变化引发的在途响应,列表查询用 `active` 标志守卫;保持既有语义(completed 过滤、首个 Run 自动选中、value 升序、前 20 条)。验证:竞态与切换单测通过。
- [x] 1.3 `SOURCE_VARIANTS` 改为 `Object.hasOwn` 自身键查找,未知 `source`(含 `constructor`/`__proto__`)原样渲染并使用中性 outline 徽标。验证:未知值渲染测试通过。

## 2. 测试

- [x] 2.1 新增 `web/src/components/evaluation/run-report-view.test.tsx`(自足 mock `evaluationApi`):覆盖列表失败与重试、真实空列表空态、分布/评分部分失败隔离、切换 Run 隐藏旧内容并收起展开、迟到成功/错误丢弃、A→B→A、重试针对当前 Run、原型属性名 `source` 原样渲染。验证:`npx vitest run` 该文件全部通过。
- [x] 2.2 核对既有 `evaluation-views.test.tsx` 的 `RunReportView` describe 在新状态机下保持通过;如 mock 需补充字段,仅做不改变既有断言语义的适配。验证:该文件全部通过。

## 3. 门禁与收尾

- [x] 3.1 运行前端门禁并留存实际结果:`npm run lint`、`npx tsc --noEmit`、生产构建 `npm run build`、全量 `npx vitest run`;确认无 `@ts-ignore`/`as any`。验证:全部 0 错误。
- [x] 3.2 运行适用共享检查与 OpenSpec 校验:`bash scripts/dev.sh check all`、`openspec validate "step10-run-report-query-state-hardening" --strict` 及 `openspec validate --specs`(如已同步);功能清单无需新增条目(既有 dashboard 能力内的呈现补强),在 change 目录写 `verification.md` 记录实际结果与未测范围。验证:全部通过并完成记录。

## 4. Review 修正与补充验证

- [x] 4.1 修正未选择 Run 时低分区域误报无评分：列表加载、失败及无 completed Run 均显示选择提示；补充五个失败回归与一个真实成功空态用例。
- [x] 4.2 补测同一 Run 时间窗口重查后的迟到成功/错误、单侧重试与另一侧在途请求隔离、切换后评分重试的 Run 归属；Run 切换测试先打开下拉框再选项，并等待选择器当前值完成呈现。
- [x] 4.3 同步 design 与 spec 的实际状态机、两路请求序号及未知 source 的中性 outline 徽标描述。
- [x] 4.4 重新运行完整前端测试、lint、类型检查、生产构建、共享检查及严格 OpenSpec 校验，更新 verification.md 的实际 review 结果。
