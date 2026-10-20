# Design

## Context

`CompareView`(`web/src/components/evaluation/compare-view.tsx`)消费 `evaluationApi.listRuns()` 与 `evaluationApi.compareRuns(baselineId, candidateId)`(`POST /api/evaluation/runs/compare`)。现状缺陷:`.catch(() => setRuns([]))` 吞掉列表失败;`result`/`error` 是无标识单状态,选择变化既不清除也不使在途请求失效,旧 Run 对的迟到响应可覆盖新选择;错误解析 `"error" in err` 与真实形状不符——api-client 非 2xx 抛出**响应体原文**,即 `{detail: {error: {code,message}}}`(404/403)或 `{detail: "<string>"}`(比较端点 422:"runs reference different datasets…"),现解析全部落到兜底文案;相同 Run 时 `compare()` 静默返回,按钮未禁用。后端比较端点错误:404/403 为对象信封,422(数据集不同/评测器集不相交)为**字符串** `detail`。

## Goals / Non-Goals

**Goals:**

- Run 列表与比较区各自有完整状态机;错误按两种 `detail` 形状解析并展示 `code/message`;相同 Run 禁用并提示。
- 比较结果绑定有序 Run 对:选择变化清除并失效旧请求,迟到成功/错误(含回切)丢弃,新比较隐藏上次结果,重试针对当前对。
- 保持既有成功路径渲染与既有 `evaluation-views.test.tsx` 用例。

**Non-Goals:**

- 不做分页、评测详情页、Run Report/Online/Annotation 视图改动,不改后端。
- 不修改 `@/lib/api-client`、`@/lib/task-types` 及共享 skill 文件;不统一其他视图的错误解析(各自切片处理)。

## Decisions

1. **列表三态 + 比较四态。** `runsState: loading|error|ready`（empty 由 ready 数据派生），`compareState: idle|comparing|error|ready`。比较内容由有序 `(baselineId, candidateId)` 的 React key 隔离，请求闭包固定这一对 ID；不另建共享 hook 或数据层。

2. **有序 Run 对实例隔离，实例内单调序号守卫。** baseline/candidate 实际变化时同批重置 `submitSeq=0` 并重建 `CompareContent`，同步清除结果、错误与加载状态；旧实例 cleanup 使请求序号失效。回切 (A,B) 是新的未提交实例，不自动复用旧提交。实例内每次 Compare/重试递增序号，仅最新请求可更新状态。下拉框会对同值重选同样触发回调，因此必须在父级先比较 ID；值未变化时不重置提交计数或失效请求，否则会丢弃当前响应并停留在 comparing。Run 列表首次加载与失败重试使用 effect `active` 标志守卫。

3. **错误解析为组件内纯函数,兼容两种 `detail` 形状。** `parseCompareError(err)` 依次解析:`detail.error.{code,message}`(平台信封)→ `detail` 为字符串(比较端点 422)→ 顶层 `error.{code,message}`(api-client 解析失败时的兜底形状)→ `Error.message` → 固定文案。列表与比较均使用该解析器，未知错误的默认文案按区域指定。放在组件文件内而非 lib:仅服务当前视图,且边界要求不改共享模块。展示格式沿用 `[CODE] message` 约定。

4. **相同 Run 禁用 + 提示。** 按钮禁用条件加入 `baselineId === candidateId`;两选择齐备且相同时,在选择区显示"Baseline 与 Candidate 不能是同一个 Run。"提示。`compare()` 的同 ID 早退保留为最后防线。

5. **新比较期间隐藏上次结果。** `comparing` 状态渲染专用占位(不渲染旧 `ready` 内容):用户发起新比较即明确旧结果不再适用,明确当前只显示新请求的加载状态。既有 `evaluation-views.test.tsx` 用例在提交后等待结果出现,不受影响。

## Risks / Trade-offs

- [选择变化即清除结果,用户误触选择会丢掉已生成的比较] → 这是 spec 规定的语义(结果必须绑定有序 Run 对);重新 Compare 一次即可恢复,无持久状态损失。
- [同一 Run 对可再次发起 Compare，先发请求可能更晚返回] → 每次提交/重试递增实例内序号，旧成功与旧错误均丢弃；测试同时覆盖 Run 对回切及同对的新旧请求。
- [比较中再次点击 Compare] → 当前按钮保留 Compare 文案，比较区单独显示“比较进行中”；新提交替换旧请求的显示资格，不新增后台自动比较或轮询。

## Migration Plan

纯前端单组件增量:改 `compare-view.tsx`、新增 `compare-view.test.tsx`,必要时微调既有测试 mock。随正常前端发布交付;回退即还原组件,无后端契约与持久状态变更。

## Open Questions

(无——状态分列、Run 对绑定与丢弃规则均已在 delta spec 固定;422 字符串错误的展示文案即后端原文。)
