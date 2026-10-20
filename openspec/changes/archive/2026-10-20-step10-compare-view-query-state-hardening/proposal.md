# Proposal

## Why

评测 Compare 视图(`web/src/components/evaluation/compare-view.tsx`)存在四类呈现与竞态缺陷:Run 列表失败被吞成空列表(`.catch(() => setRuns([]))`);切换 baseline/candidate 后旧比较结果仍留在页面上;比较请求没有任何竞态守卫,在途响应用无标识的单状态保存,可覆盖新选择的 Run 对;错误解析(`"error" in err`)与后端实际错误形状不符——api-client 非 2xx 时抛出的是响应体原文 `{detail: {error: {code,message}}}`,而比较端点的 422(数据集不同)返回**字符串** `detail`,两者都被降级为兜底文案"Comparison failed",用户看不到 404/403/422 的真实原因。另外相同 Run 的比较仅静默返回,按钮未禁用也无提示。

## What Changes

- Run 列表区分加载中、成功空态与失败,失败消费 `detail.error.{code,message}` 并提供重试;真实空列表呈现无可选 Run 的空态而非错误。
- 比较区区分未提交、比较中、成功、失败四种状态;新一次比较开始时隐藏上一次结果;失败展示后端 `code/message`,兼容比较端点 422 返回的字符串 `detail`,提供针对当前选择的重试。
- 比较结果绑定有序 Run 对:baseline 或 candidate 改变立即清除旧结果与旧错误并使在途请求失效;丢弃迟到成功、迟到错误,以及 A/B→A/C→A/B 回切后首个请求的迟到响应;重试始终使用当前选择。
- 两个 Run 相同时禁用 Compare 按钮并给出明确提示,不再静默返回。
- 保持既有成功路径语义:completed 过滤、结果渲染(逐指标 delta、配对 deltas、drift 徽标)与既有回归判定展示不变。
- 不扩展分页、评测详情页或后端;不修改全局 API client、`@/lib/task-types`、Run Report 视图、Runtime 及共享 skill 文件。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-report-dashboard`: 修改"Compare view"要求——补充未提交/比较中/失败状态区分、结构化与字符串错误消费、相同 Run 禁用并提示;新增"Compare 查询绑定有序 Run 对并丢弃迟到响应"要求(选择变化清除并失效旧请求、迟到成功/错误与回切丢弃、重试使用当前 Run 对、新比较期间隐藏上次结果)。

## Impact

- 主要修改 `web/src/components/evaluation/compare-view.tsx`(单一组件);新增 `web/src/components/evaluation/compare-view.test.tsx`。
- 既有 `evaluation-views.test.tsx` 的 CompareView describe(比较成功用例)保持通过,必要时小幅更新 mock。
- 仅导入复用 `@/lib/api-client` 的 `evaluationApi`;错误解析为组件内纯函数(两种 `detail` 形状),不改动共享模块。
- 契约归属既有能力 `evaluation-report-dashboard`。
