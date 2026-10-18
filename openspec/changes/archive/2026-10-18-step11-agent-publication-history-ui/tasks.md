# Tasks

## 1. 类型与 API helper

- [x] 1.1 新增 `web/src/lib/publication-types.ts`：`PublicationItem`（对齐 `AgentPublicationReadSchema` 白名单，可空字段显式 `| null`）、`PublicationListResponse`（`{items, total}`）、`publicationApi.list(agentId, offset, limit=PUBLICATION_LIST_PAGE_SIZE)`（拼真实 query string，复用导出的 `api` 单例）、`publicationErrorMessage`/`publicationErrorCode`（消费 `detail?.error?.{code,message}`）、`OUTCOME_LABELS` 四值 map 与 `outcomeLabel()`（未知回退原文，缺失不得显示为通过语义）。验证：`npx tsc --noEmit` 通过；`publicationApi.list` 拼出的 URL 与后端路由一致。

## 2. 面板组件

- [x] 2.1 新增 `web/src/components/agent/publication-history-panel.tsx`：接收 `agentId` prop；identity-tagged response guard（`{agentId, offset}` 标签）+ effect `active` flag；加载/空/错/就绪四态；错误态消费 code/message 并提供重试；刷新按钮在全部状态可用；空历史（`total===0`）显示"暂无发布凭据"及早期发布可能没有记录的说明，`items` 空但 `offset>0` 显示"当前页暂无发布凭据"并保留上一页。验证：组件级测试覆盖各状态切换。
- [x] 2.2 列表行以凭据 `id` 为 key 展示发布时间、版本、操作人（缺失→"未记录"）、结论徽章（`outcomeLabel` 四值文案）；分页条显示总数/页码，上一页/下一页按 `total` 与当前 offset 禁用。验证：测试断言同版本两条凭据渲染为独立两行、分页按钮禁用边界正确。
- [x] 2.3 行展开证据区：展示 `agent_config_hash`、candidate/baseline Run id 与配置哈希（可空字段显示"未记录"行位）、`gate_config` 与 `gate_report` 快照（已知字段结构化渲染：mode/bypassed_by_force/deterministic_pass_rate/signals；未知畸形回退原始 JSON 展示）。验证：测试断言展开显示冻结字段、baseline 缺失如实呈现、畸形快照不崩溃。

## 3. 详情页装配

- [x] 3.1 `web/src/app/(dashboard)/agents/[id]/page.tsx` 在 `VersionPanel` 后加一行 `<PublicationHistoryPanel agentId={agentId} />`；不改其他装配与逻辑。验证：详情页测试（或既有页面冒烟）确认面板入口渲染。

## 4. 测试补全

- [x] 4.1 新增 `web/src/__tests__/publication-history.test.tsx`：四值 outcome 文案互不混用（含 gate_off 无评测通过语义、force_bypassed 含绕过语义）；历史行不标"当前生效版本"；展开证据为凭据快照（后续新评测不改变展示）；空历史语义（不推断"从未发布"）；错误结构消费与重试；翻页竞态与 A→B→A 同参竞态（deferred promise 控制返回顺序）；空后续页保留上一页；各状态刷新可用。验证：`npx vitest run` 全部通过。
- [x] 4.2 请求 URL 断言：`limit=20&offset=N` 按页递增；刷新重发当前 offset。验证：测试内 `api.get` 调用参数断言。

## 5. 共享检查与验证记录

- [x] 5.1 运行 `web/` 下 lint、TypeScript 检查、vitest 全套与 production build（`npx next build`）。验证：0 错误；如既有失败与本次无关，逐项调查并记录。
- [x] 5.2 运行适用的仓库共享检查（`bash scripts/dev.sh check all` 或同源的 Python CLI）。验证：0 错误或明确记录不适用项。
- [x] 5.3 汇总实际验证结果到本 change 的 `verification.md`：列出测试/构建/检查的真实输出要点、跳过项与保留限制（不得写预期结果）。验证：文件存在且内容与实际命令输出一致。

## 6. 审查修正

- [x] 6.1 修复 Agent 切换时继承旧分页和展开状态、后续页失败丢失分页导航、缺失 bypass 证据误显示为否、缺失配置无标签、可空 Run ID 隐藏哈希、原型属性 outcome 回退；新增真实 A→B→A 回归。验证：先复现 8 个失败，修复后本 change 26 个测试通过。
- [x] 6.2 修正 design 中 identity 标签与 effect cleanup 作用颠倒的说明，记录真实验证范围。
