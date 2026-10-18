# Proposal

## Why

#276 已在每次成功发布时落盘只追加的发布凭据（candidate/baseline 评测 Run 身份与配置哈希、发布时门禁配置与报告快照、封闭四值 outcome、操作人与时间），并提供 `GET /api/agents/{agent_id}/publications` 只读历史查询；但这些凭据目前没有任何查看入口——"当时为什么被放行"的证据只有 API 能答，Studio 用户无法查看。本变更把已有证据变成可查看的历史面板，让发布审计真正可用。

## What Changes

- 在 Agent 详情页新增独立"发布历史"面板（新组件 `PublicationHistoryPanel`），装配行仅加一行；消费既有 `GET /api/agents/{agent_id}/publications?limit=&offset=` 查询。
- 列表展示发布时间、版本、操作人、发布结论；结论文案按 outcome 四值区分：`passed`→门禁通过、`warn_passed`→告警模式放行、`force_bypassed`→强制放行（绕过门禁）、`gate_off`→门禁未启用；`warn_passed`/`force_bypassed`/`gate_off` SHALL NOT 统一显示为"评测通过"，历史记录 SHALL NOT 标示为"当前生效版本"。
- 单条凭据可展开查看发布时点证据：版本配置哈希、candidate/baseline 评测 Run 的 id 与配置哈希、冻结的 `gate_config` 快照与 `gate_report` 报告摘要；一律读凭据快照，SHALL NOT 查询最新评测替换历史结论。同版本重复发布的凭据分别成行，以凭据 `id` 为行标识。
- 空历史显示"暂无发布凭据"，并说明早期发布可能没有记录（凭据能力引入前不回填），SHALL NOT 推断为"从未发布"。
- 查询边界：加载中、空数据、查询失败三态互不冒充；失败展示 `detail.error` 的 code/message 并可重试；Agent 切换、翻页、手动刷新时过期成功响应与过期错误 SHALL NOT 覆盖当前内容；后续页变空时保留返回上一页入口。
- 分页与手动刷新；不加入重新发布、回滚、门禁重算或自动轮询（发布后可手动刷新历史）。

## Capabilities

### New Capabilities

- `agent-publication-history-ui`: Agent 发布历史只读面板的行为契约——凭据列表与展开证据的呈现规则、outcome 四值文案的准确性约束（gate_off/force_bypassed/warn_passed 不得读作评测通过）、同版本多凭据以 id 为行标识、空历史语义、分页/刷新/竞态守卫与三态独立性。纯前端消费切片，不改变任何后端契约。

### Modified Capabilities

（无——后端查询行为已由 `agent-versioning` 的"发布历史只读查询" requirement 覆盖，本变更不改变其任何 requirement。）

## Impact

- **新增**：`web/src/components/agent/publication-history-panel.tsx`（面板组件）；`web/src/lib/publication-types.ts`（凭据类型、`publicationApi` helper、outcome 文案 map——复用导出的 `api` 单例，不改 `api-client.ts`）；对应测试文件。
- **修改**：`web/src/app/(dashboard)/agents/[id]/page.tsx`（仅加一行面板装配）。
- **不修改**：`VersionPanel` 发布逻辑、全局 `api-client.ts`、任何后端文件（`src/hecate/**`）、Task 观察页面。
- **依赖**：后端 `GET /api/agents/{agent_id}/publications`（#276 已合入 origin/main，响应信封 `{items, total}`，白名单 schema 见 `AgentPublicationReadSchema`）。无需数据库迁移、无 CI 门禁变化。
