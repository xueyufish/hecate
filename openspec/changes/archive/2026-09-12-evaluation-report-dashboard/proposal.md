# Proposal: evaluation-report-dashboard（7.2e Evaluation Report Dashboard）

## Why

7.2c 与 7.3 已交付完整的评估数据层（offline run/scores、online target-typed scores、runs-compare API），但全库没有任何聚合端点——无成功率汇总、无分数分布、无时序趋势、无维度下钻；`web/` 前端也零评估 UI。2026-09 竞品调研（Azure AI Foundry、Bedrock AgentCore、AgentScope/OpenJudge、Langfuse/LangSmith 等）显示业界已收敛出一致的评估报告范式：**卡片 + 趋势 + 分布 + 配对 delta 对比 + 分数→trace 下钻**。roadmap 已将 7.2c 延后的 session rollup 视图与 7.3 延后的对比视图 UI 记入 7.2e 承接，现在交付。

## What Changes

- **后端聚合层**（只读、全部按需计算、零新表零 migration）：
  - `GET /api/evaluation/reports/overview` — 四卡聚合：质量（offline pass_rate + online 均分）、规模（近期 runs 完成数 + online scored 条数）、覆盖度（活跃 dataset + 低样本 run 占比）、评估错误率（value=-1 占比）
  - `GET /api/evaluation/reports/trends` — pass_rate / 分数时序，按 dataset | workflow | agent 维度
  - `GET /api/evaluation/reports/distributions` — 每 metric 分数直方图（排除 error 值 -1）
  - `GET /api/evaluation/reports/breakdowns` — 在线分数 group by agent | task | session | source
  - `GET /api/evaluation/reports/sessions` — session rollup 视图（7.2c 延后项的视图部分；session 级打分不在本变更）
- **前端评估页** `web/`：`(dashboard)/ops-center/evaluation`（ops-center 第四个入口），四个视图：
  - 总览：四卡 + 趋势图（对齐 Azure Monitor dashboard 骨架）
  - 在线质量：采样预算头部条（`tasks.metrics` 的 sampled/上限/错误率）+ agent × metric 图 + session 下钻
  - Run 报告：分数直方图 + 维度图 + 低分样本列表（点击看 reasoning，按 `source` 分色）
  - 对比视图：渲染既有 `POST /api/evaluation/runs/compare`（pass-rate lift + token/latency/cost 配对 delta + drift；承接 7.3 的 UI 欠账）
- **侧边栏导航项**：ops-center 下新增 Evaluation 入口。

## Non-goals（延后事项，archive 时同步 roadmap 与 feature-catalog）

| # | 延后项 | 去向 | 重启触发条件 |
|---|---|---|---|
| 1 | Session 级多轮**打分**（session-scoped input projector） | 7.2d（与人工标注/trace 回流同域） | 7.2d 立项时承接；本变更只交付 rollup 视图 |
| 2 | 人评队列 / annotation queue | 7.2d | 同上；本变更仅按 `source=human` 分色展示已有数据 |
| 3 | 失败聚类（cluster analysis） | OE3 / 独立特性 | 分数积累到有统计意义的量且 reasoning 可结构化出错误类型 |
| 4 | 质量回归告警 | OE3（roadmap 已列） | OE3 启动；约束：trends 端点保证阈值可计算（见 design.md） |
| 5 | 评估器版本锁定（Bedrock evaluator lock 模式） | 独立特性（建议 feature ID 7.2f） | 多租户出现 evaluator prompt 改动导致的评分口径漂移 |
| 6 | join `traces` 拿 Model/Tool 聚合粒度 | v1.5 | 出现 per-model / per-tool 质量分桶需求；v1 只用反规范化的 `agent_id`/`session_id` |
| 7 | judge token 消耗埋点（evaluator usage 记录） | 独立小增量 | 成本叙事需求（OE3）或评估成本投诉 |
| 8 | compare 通用化（跨时间退化对比，非 run vs run） | 本变更交付后的加法 | 用户要求在趋势上直接做 delta 对比 |

## Capabilities

### New Capabilities

- `evaluation-report-dashboard`：评估报告聚合 API（overview/trends/distributions/breakdowns/sessions 五个只读端点）+ ops-center 评估页四视图（总览/在线质量/Run 报告/对比）+ 侧边栏入口。命名沿用 `cost-dashboard`、`conversation-analytics-dashboard` 先例。

### Modified Capabilities

（无 — 不改变 `evaluation-api`、`evaluation-tasks`、`workflow-evaluation` 的既有需求；本变更只消费它们的数据。）

## Impact

- **后端**：新增 `src/hecate/ops/evaluation/reports/`（聚合 service）与 `src/hecate/ops/api/evaluation_reports.py`（router 注册进 main）；只读查询现有表 `evaluation_runs` / `evaluation_scores` / `evaluation_tasks` / `evaluation_task_scores` / `evaluation_datasets` / `evaluation_items`，无 schema 变更。
- **前端**：新增 `web/src/app/(dashboard)/ops-center/evaluation/` 页面与子视图，复用 Recharts 组件与 `lib/api-client.ts`；侧边栏组件加一个导航项。
- **API 兼容性**：纯新增只读端点，无 breaking change。
- **成本**：聚合查询走现有索引（run_id/task_id/session_id/workspace_id），默认时间窗约束（30 天）防全表扫描。
