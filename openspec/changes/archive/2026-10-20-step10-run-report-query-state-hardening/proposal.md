# Proposal

## Why

评测 Run Report 视图(`web/src/components/evaluation/run-report-view.tsx`)当前把查询失败吞成空数据:`listRuns`/`getDistributions`/`listRunScores` 的任何失败(含 step10 读取权限边界引入的 403、404 与服务异常)都被渲染为"没有评分",真实空态与不可用不可区分;同时切换 Run 没有任何迟到响应守卫,旧 Run 的分布或低分列表可能覆盖当前选择,造成评分证据串用。该视图是发布评测证据的人工观察入口,展示错误会直接误导质量判断。

## What Changes

- Run 列表、评分分布、低分列表三路查询各自区分加载中、成功空态与失败;失败消费并展示 `detail.error.{code,message}` 并提供重试,重试始终针对当前选中 Run。
- 分布与评分两路独立处理:任一路失败不清除、不掩盖另一路已成功的结果;失败不得呈现为"No scores recorded for this run."。
- 切换 Run 后立即隐藏旧 Run 内容并清空展开行;旧成功、旧错误及 A→B→A 回切的迟到响应一律丢弃;时间窗口参数(`startDate`/`endDate`)变化按当前 Run 重新查询并受同样守卫约束。
- 呈现健壮性:未知 `source` 值(包括 `constructor` 等对象原型属性名)原样回退显示,不产生渲染异常。
- 保持既有展示语义:仅 `completed` Run 可选、首个 Run 自动选中、评分按 value 升序、低分列表取前 20、行展开显示 reasoning 与 source 徽标。
- 不扩展分页、评测详情页、比较视图或后端;不修改全局 API client、`@/lib/task-types`、Task 页面及 Runtime。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `evaluation-report-dashboard`: 修改"Run report view"要求(补充未知 `source` 原样回退的场景);新增"Run report 查询状态明确呈现"与"Run 报表查询的迟到响应丢弃"两条要求——三路查询状态分列、错误结构与重试、部分失败隔离、切 Run 视图重置与竞态丢弃。

## Impact

- 主要修改 `web/src/components/evaluation/run-report-view.tsx`(单一组件);新增 `web/src/components/evaluation/run-report-view.test.tsx`。
- 现有 `evaluation-views.test.tsx` 的 `RunReportView` describe(展开明细用例)保持通过,必要时小幅更新 mock 断言。
- 复用 `@/lib/api-client` 的 `evaluationApi` 与 `@/lib/task-types` 的错误解析助手(仅导入,不修改);沿用现有 REST 端点,无后端改动。
- 契约归属既有能力 `evaluation-report-dashboard`(step10 读取权限边界 #287 的后续补强)。
