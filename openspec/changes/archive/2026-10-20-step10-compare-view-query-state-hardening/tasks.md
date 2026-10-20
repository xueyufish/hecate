# Tasks

## 1. CompareView 状态机改造

- [x] 1.1 改造 `web/src/components/evaluation/compare-view.tsx`:Run 列表区分 loading/error/ready(空态派生),失败消费 `detail.error.{code,message}` 并提供重试;比较区实现 idle/comparing/error/ready 状态机,新比较开始时隐藏上次结果,失败展示后端 `code/message` 与针对当前 Run 对的重试。验证:组件断言测试通过。
- [x] 1.2 组件内新增 `parseCompareError` 纯函数:依次解析 `detail.error.{code,message}`、字符串 `detail`(比较端点 422)、顶层 `error.{code,message}` 兜底与 `Error.message`,替换现有 `"error" in err` 解析。验证:单测覆盖两种 `detail` 形状。
- [x] 1.3 比较结果绑定有序 Run 对:以有序 Run 对为 key 的内容实例固定请求标识；实际选择变化时同批重置提交计数并重建为 idle，旧实例 cleanup 使请求失效；相同选项重选保持当前请求;相同 Run 时禁用 Compare 按钮并显示提示。保持既有成功路径渲染(逐指标 delta、回归徽标、配对 deltas、drift)。验证:竞态与禁用单测通过。

## 2. 测试

- [x] 2.1 新增 `web/src/components/evaluation/compare-view.test.tsx`(自足 mock `evaluationApi`):覆盖列表失败与重试、真实空列表空态、结构化错误(404 信封)与字符串错误(422)分别展示、相同 Run 禁用与提示、选择变化清除旧结果、迟到成功/错误丢弃、A/B→A/C→A/B 回切、重试使用当前 Run 对、新比较期间隐藏上次结果。验证:`npx vitest run` 该文件全部通过。
- [x] 2.2 核对既有 `evaluation-views.test.tsx` 的 CompareView describe(比较成功渲染 deltas)保持通过;如 mock 需适配新状态机,仅做不改变既有断言语义的调整。验证:该文件全部通过。

## 3. 门禁与收尾

- [x] 3.1 运行前端门禁并留存实际结果:`npm run lint`、`npx tsc --noEmit`、生产构建 `npm run build`、全量 `npx vitest run`;确认无 `@ts-ignore`/`as any`。验证:全部 0 错误。
- [x] 3.2 运行适用共享检查与 OpenSpec 校验:`bash scripts/dev.sh check all`、`openspec validate "step10-compare-view-query-state-hardening" --strict`;功能清单无需新增条目(既有 dashboard 能力内的呈现补强),在 change 目录写 `verification.md` 记录实际结果与未测范围。验证:全部通过并完成记录。

## 4. Review 修正与验证

- [x] 4.1 仅在 baseline/candidate 实际变化时重置提交计数；相同选项重选不得作废在途比较或重试。
- [x] 4.2 Run 列表同样消费组件内错误解析器，保留字符串 detail 及 API client 顶层 error 的 code/message。
- [x] 4.3 强化测试：真实打开选择菜单、同值重选、已成功/失败状态随任一选择变化清除、回切迟到错误、同对并发与有序 Run 对方向；更新设计与规范描述。
- [x] 4.4 执行相关和完整前端测试、lint、类型检查、生产构建、共享检查、OpenSpec strict，并记录实际结果及未测范围。
