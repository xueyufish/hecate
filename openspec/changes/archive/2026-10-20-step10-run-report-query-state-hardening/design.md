# Design

## Context

`RunReportView`(`web/src/components/evaluation/run-report-view.tsx`)消费 `@/lib/api-client` 的三个端点:`evaluationApi.listRuns()`(无参)、`evaluationApi.getDistributions({run_id})`、`evaluationApi.listRunScores(runId)`。现状缺陷:`useEffect` 内 `.catch(() => setRuns([]))`、`loadRun` 内 `catch { setDistributions(null) }` 与 `catch { setScores([]) }` 把一切失败吞成空数据;`loadRun` 无迟到响应守卫,`useEffect` 依赖 `[runId, loadRun, startDate, endDate]`;切换 Run 不清空 `expandedScore`。错误信封为平台统一的 `HTTPException(detail={"error":{code,message,...}})`(`src/hecate/ops/api/evaluation.py`),与 `@/lib/task-types` 的 `taskErrorCode/taskErrorMessage` 解析形状一致。既有能力 `evaluation-report-dashboard` 的"Run report view"要求固定了展示语义(completed 过滤、自动选中、value 升序、前 20 条、reasoning 展开、source 徽标);#287 为评测 Run 读取加了 workspace 访问边界(403/404),本切片是其前端呈现补强。

## Goals / Non-Goals

**Goals:**

- 三路查询各自持有 loading/empty/error 状态,失败可见、可重试、不冒充空数据;两路数据区相互隔离。
- 用成熟的两级守卫(run 标识匹配 + 单调请求序号,同 `task-commands-panel` 的 keyed 实例模式)消除迟到响应覆盖。
- 保持全部既有展示语义与取数范围,不触碰 API client 与后端。

**Non-Goals:**

- 不做分页、Run 报表之外的视图(Compare/Online/Annotation)、评测详情页或后端改动。
- 不修改 `@/lib/api-client`、`@/lib/task-types`、Task 页面、Runtime。
- 不改变 `startDate/endDate` 的既有语义(它们当前仅作为重查触发器,不进入请求参数——本次如实保留,并让其重查受守卫约束)。

## Decisions

1. **单组件内拆分三个查询状态,不新建数据层。** `runs`、`distributions`、`scores` 各自有 `{kind: "loading"|"error"|"ready"}` 状态机；empty 是 ready 且数据为空的呈现分支。分布与评分由各自请求函数并行加载，独立 try/catch 与 setState，互不回滚对方。备选"抽公共 useQuery hook"被否决:两路请求的生命周期完全同run,抽象只服务一个消费者,违背"无第二消费者不建扩展点"。

2. **run 切换用 keyed 内容实例重置,run 内守卫用单调序号。** 外层 `RunReportView` 以 `runId` 为 React `key` 渲染报表内容组件,切换 Run 即重建:分布/评分/展开行整体回到加载态,结构上杜绝旧内容残留;实例内独立的 `distSeq` / `scoreSeq` 使重试与窗口参数变化(`startDate/endDate` 依赖)引发的在途旧响应失效。这与 `task-commands-panel` review 修正后的模式一致,直接复用已验证的结构。单侧重试只递增自己的序号，不能使另一侧在途请求失效。Run 列表的首次加载与失败重试单独用 effect `active` 标志守卫。

3. **错误结构复用 `taskErrorCode/taskErrorMessage`(仅导入)。** 评测端点与任务端点共用平台 `detail.error.{code,message}` 信封,助手是纯函数;备选"在组件内重写解析"被否决:重复实现同一信封解析,且 `evaluation-views.test.tsx` 已有同文件导入先例。

4. **列表失败集中在 Run 卡片呈现,数据区保持"未选择"语义。** `listRuns` 失败时 Run 选择区显示 `code/message` + 重试;分布/评分区在列表加载中、失败或成功无可选项时，均显示未选择 Run 的引导文案，不宣称某个 Run 没有评分。只有选中 Run 的对应查询成功且结果为空，才显示无评分空态。`completed` 过滤与首个 Run 自动选中逻辑保持不变;过滤后为空是成功空态。

5. **`SOURCE_VARIANTS` 查找改为自身键判断。** 与 `task-command-types` 同类问题:`Record` 直接索引会把 `constructor` 解析为原型函数传入 Badge。用 `Object.hasOwn` 守卫,未知值渲染原字符串、使用中性 `outline` 徽标，不赋予已知来源的特定含义;作为"Run report view"要求的 MODIFIED 场景固定。

## Risks / Trade-offs

- [窗口参数变化触发整 run 重查(既有行为)在慢网络下可能闪烁加载态] → 保持既有语义不变;守卫仅保证不串数据,不引入缓存。闪烁是现状已有行为,本次不扩大处理范围。
- [keyed 重挂载使 Run 切换丢展开行] → 符合 spec(切 Run 收起展开);同 Run 内的展开不受影响(重试/重查不重挂载)。
- [既有 `evaluation-views.test.tsx` 断言与新状态机的交互] → 既有用例只覆盖成功路径的展示语义,预期不受影响;若 mock 缺少新用例依赖的字段,在新测试文件内自足 mock,不改既有断言语义。

## Migration Plan

纯前端单组件增量:改 `run-report-view.tsx`、新增 `run-report-view.test.tsx`,必要时微调 `evaluation-views.test.tsx` 的 mock。随正常前端发布交付;回退即还原该组件,无后端契约与持久状态变更。

## Open Questions

(无——状态分列、竞态丢弃与隔离边界均已在 delta spec 固定;错误文案措辞属实现自由度。)
