# Design

## Context

后端已交付（origin/main `9c95918a`，#276）：`GET /api/agents/{agent_id}/publications` 位于 `src/hecate/studio/api/agent_versions.py`，`limit`（1–100，默认 20）+ `offset`（≥0），响应信封 `{items, total}`——**无 limit/offset 回显**，分页状态由前端自持。条目为 `AgentPublicationReadSchema` 白名单：`id, agent_id, version, agent_config_hash, candidate_run_id/hash, baseline_run_id/hash, gate_config, gate_report, outcome, operator_user_id, created_at`。可空字段：`operator_user_id`、`candidate_run_id/hash`、`baseline_run_id/hash`、`gate_config`、`gate_report`。404 经 `_not_found` 包装为 `detail.error.{code,message}`；凭据按 `created_at desc, id desc` 排序。

前端现状：Agent 详情页 `web/src/app/(dashboard)/agents/[id]/page.tsx` 装配 `AgentConfigurator + VersionPanel + ChannelManager`；`VersionPanel` 的发布逻辑走全局 `agentVersionsApi`（`api-client.ts`）。任务观察切片（step14）已建立可复用模式：`task-types.ts` 的"类型 + api helper + 文案 map"单文件模式、identity-tagged response guard、effect `active` flag、`vi.hoisted` API mock、`Table`/`Badge` UI 组件。

## Goals / Non-Goals

**Goals:**

- 一个自包含面板组件：列表 + 展开证据 + 分页 + 刷新 + 三态与竞态守卫。
- 复用 step14 已验证的模式与 UI 基件，新代码集中在两个新文件加一行装配。
- 与 `task-run-observation-ui` 同等的测试深度：状态准确性（四值文案、空历史语义）、冻结证据、竞态、分页、错误结构。

**Non-Goals:**

- 不改 `VersionPanel`、全局 `api-client.ts`、任何后端文件。
- 不做重新发布、回滚、门禁重算、自动轮询。
- 不做凭据间 diff、按版本/outcome 过滤、跨 Agent 聚合视图（可后续按需追加）。

## Decisions

1. **新 API helper 走独立 `web/src/lib/publication-types.ts`，复用导出的 `api` 单例**（同 `task-types.ts` 先例）。备选：把 `publicationApi` 加进 `api-client.ts`——被 proposal 边界明确排除；独立文件同样享受 auth 刷新复用，且使变更面最小。
2. **请求生命周期与查询身份共同保护**：effect 每次执行各自拥有 active 标志，cleanup 后成功与失败响应均不得更新状态，因此同参刷新、Strict Mode replay 与 A→B→A 均由请求生命周期保护；agentId/offset 标签用于查询身份改变时立即隐藏旧内容，不能代替 active 标志。外层面板按 agentId 为内层组件设置 key，Agent 改变时同步重置分页、展开状态与已知总数；不先向新 Agent 请求旧 offset。

3. **行 key 与展开状态均以凭据 `id`（uuid）为标识**。`version` 只是展示列——同版本多凭据并存是后端明确语义（append-only），按 version 作 key 会碰撞。展开状态存 `Set<string>`（id），切页保留各自展开状态亦可接受（不强求跨页记忆，切 Agent 时随状态重置）。
4. **文案 map 与状态徽章集中在 `publication-types.ts`**：`OUTCOME_LABELS` 四值 + 未知回退原文（复用 `outcomeLabel` 模式与注释纪律："Outcome is a recorded fact; absence means not recorded"）。组件内不散落三元表达式文案。
5. **展开证据区分已知字段结构化渲染 + 兜底**：`gate_report` 已知形状（`mode`、`bypassed_by_force`、`deterministic_pass_rate`、`signals[]`）按字段渲染，其他字段（包括 regressions）可在原始报告中查看；未知/畸形快照回退为可滚动的原始 JSON 展示——快照是历史事实，宁可原样展示也不丢弃或美化。可空字段（baseline、operator、candidate、配置）显示"未记录"并在证据区保留带标签的行位；Run ID 和配置哈希分别呈现，缺少 ID 不隐藏哈希。报告缺失 bypassed_by_force 时显示"未记录"，仅显式 false 才显示"否"（语义化缺失，不静默消失）。
6. **分页参数直接用 offset/limit**（与后端一致），页面尺寸常量 `PUBLICATION_LIST_PAGE_SIZE = 20`。响应无 offset 回显，`total` 用于算总页数；空页判定 `items.length === 0 && offset > 0` 时显示"当前页暂无发布凭据"并保留上一页。总数单独保存为最后一次成功查询的结果，后续页加载或失败时保留分页导航，初次查询未知总数时不虚构计数。
7. **测试沿用 `vi.hoisted` + `vi.mock("@/lib/api-client")` 模式**（`version-panel.test.tsx` 先例），断言真实请求 URL（含 limit/offset）；竞态用 deferred promise 控制返回顺序（`tasks-page.test.tsx` 先例）。页面测试放 `web/src/__tests__/publication-history.test.tsx`，组件级行为（展开、文案）与面板装配（详情页渲染入口）分开断言。

## Risks / Trade-offs

- [后端 404 把"Agent 不存在"与"Agent 无凭据"分开，但前端错误态若把 404 渲染成面板错误会吓到用户] → Agent 主查询已在详情页完成（404 时整页已显示 not found），并行查询期间 Agent 的删除或权限变化仍可能使面板返回 404；仍按错误态消费 `detail.error` 呈现，不做特判吞错误。
- [`gate_report`/`gate_config` 是自由 JSON 快照，后端演进可能加字段] → 决策 5 的兜底渲染保证新字段可见不崩溃；测试覆盖畸形快照回退路径。
- [`created_at` 同秒的凭据排序依赖后端 `id desc` 次要排序] → 前端不重排，按响应顺序渲染；行 key 用 id 天然稳定。
- [面板与 VersionPanel 并列展示，用户可能把历史行当版本列表] → 面板标题与空态文案明确"发布历史/凭据"语义；决策 4 的文案 map 保证结论列永远是凭据事实而非版本状态。

## Migration Plan

纯前端增量：新增两文件 + 一行装配，无迁移、无后端依赖变化。回滚 = revert 提交即可，无数据兼容问题。

## Open Questions

（无。）
