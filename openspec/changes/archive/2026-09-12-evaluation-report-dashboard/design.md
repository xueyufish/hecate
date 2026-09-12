# Design: evaluation-report-dashboard

## Context

数据层已就绪（见 proposal.md — Why）：`evaluation_runs`（含 `summary.pass_rate`/`consistency_rate`、`workflow_id/version`、`dataset_snapshot`）、`evaluation_scores`（run × item × metric，`-1.0` 为 error 哨兵值，`source` 三通道）、`evaluation_tasks`（`metrics` 计数器 + `config.sampling_rate`/`max_traces_per_cycle`）、`evaluation_task_scores`（已反规范化 `session_id` + `agent_id`）。既有索引覆盖 run_id / task_id / session_id / workspace_id。前端 `web/`（Next.js 14 + Recharts 3 + Tailwind）已有 `(dashboard)/ops-center/{agents,conversations,tools}` 先例。本设计全部为只读聚合，不新增表、不加 migration。

## Goals / Non-Goals

**Goals:**

- 五个按需聚合端点 + 四视图评估页，零 schema 变更、零新埋点
- 聚合查询全部走现有索引，默认 30 天窗口防全表扫描
- trends 响应保证"每桶 avg + count"结构，为 OE3 告警（阈值可计算）留好数据源

**Non-Goals:**

- proposal.md Non-goals 表中的全部 8 项（session 打分、人评队列、失败聚类、告警、evaluator lock、join traces、judge token 埋点、compare 通用化）
- 服务端缓存 / 物化视图（v1 直接查询）

## Decisions

### D1. 按需聚合，不做持久化报告实体

备选：run 完成时写 `evaluation_reports` 快照行。否决理由：竞品（Langfuse/LangSmith/兄弟 dashboard）全部现算；`runs.summary` 与 `dataset_snapshot` 已经承担"冻结"职责；报告查询是低频人读场景，实时聚合的代价可接受，且省一条 migration 和回写链路。若未来数据量证明需要，加缓存层是纯增量。

### D2. offline 趋势走 `runs.summary`，不扫 `evaluation_scores`

`evaluation_scores` 没有 `created_at` 索引，按时间扫全量分数代价高；而 `evaluation_runs` 表小、`summary.pass_rate` 是 7.2c/7.3 已算好的权威聚合。offline 时序 = 有 `summary` 的 completed runs 按 `completed_at` 分桶取 `pass_rate`。代价：无 threshold 配置的 run（`summary=None`）不进质量线，只进规模卡——口径在卡片 tooltip 注明。备选（扫 scores 现算）否决：性能与口径重复计算。

### D3. online 聚合只用反规范化列，不 join `traces`

`evaluation_task_scores.session_id`/`agent_id` 就是 7.2c 为 rollup 预留的（模型 docstring 明示）。v1 的 breakdowns/sessions 全部 group by 这两列；join `traces` 拿 Model/Tool 粒度是 Non-goal #6（v1.5）。

### D4. 分桶与窗口参数约定

- 窗口：`start_date`/`end_date` 可选，默认近 30 天；上限 clamp 到 365 天。
- 桶：`day`（默认）/ `hour`；`hour` 仅允许窗口 ≤ 30 天，防桶数爆炸。
- 时区：分桶用 UTC（与 `DateTime(timezone=True)` 存储一致），前端负责本地化显示。
- 直方图：固定 10 桶覆盖 `[0.0, 1.0]`，Python 侧分桶（两种 scope 的行数都有界：`run_id` → items × metrics；`task_id` → 走 task 索引 + 窗口过滤）。

### D5. 覆盖度在查询时点计算，用 live dataset 而非 snapshot

低样本 run 比例的分母 = 窗口内 completed runs，分子 = 其 `dataset_id` 当前 items 数（`deleted=0`）< 20 的 runs。理由：覆盖度回答"现在这个数据集还够不够用"，关心 live 状态；snapshot 只服务于回归对比（7.3 的语义）。阈值 20 作为常量放在 reports service，便于日后调整。

### D6. API 落点与模块结构

- `src/hecate/ops/evaluation/reports/service.py`：纯 SELECT 聚合，函数按报告类型划分（`overview` / `trends` / `distributions` / `breakdowns` / `session_rollup`），Pydantic 响应 schema 定义在同模块（它们是报表形态，不是领域模型，不进 `models/evaluation.py`）。
- `src/hecate/ops/api/evaluation_reports.py`：`APIRouter(prefix="/evaluation", tags=["evaluation"])`，路径 `/reports/...`，鉴权沿用 `get_auth_context`（workspace 隔离与其他端点一致）；在 `main.py` 注册。
- 错误值 `-1.0` 的排除是**查询约定**，集中在 service 一处实现，避免各端点口径漂移。

### D7. 前端复用 ops-center 既有模式

`(dashboard)/ops-center/evaluation/page.tsx` 单页 + tab 切换（overview / online / runs / compare），与 conversations/tools 的 client-component + `lib/api-client.ts` 模式一致；图表只用现有 Recharts `BarChart`/`LineChart`；导航图标用 lucide-react（`ClipboardCheck`）。source 分色只在 Run 报告视图；对比视图调用既有 `POST /api/evaluation/runs/compare`，前端不重算 delta。

### D8. compare 口径约束停留在文档层

对比视图页脚提示"两个 run 应使用相同 evaluator 集合"，不做代码约束——evaluator 版本锁定是 Non-goal #5（7.2f）的事，届时 compare 才需要按 evaluator version 对齐。

## Risks / Trade-offs

- [`evaluation_task_scores` 随时间增长拖慢 online 聚合] → 窗口 clamp + 走 workspace/task 索引；v1 数据量（采样制）可控，量大后加时间分区是独立增量
- [pass_rate 简单平均存在"大 dataset 小 dataset 等权"问题] → v1 接受 run 级等权（与 Bedrock/Azure 简单成功率口径一致），卡片注明口径；按 item 加权留作加法
- [`summary=None` 的 run 不进质量线造成"质量卡与 runs 数不一致"] → tooltip 注明口径（"仅统计配置了 threshold 的 run"）
- [overview 首屏多端点串行造成加载慢] → overview 一次返回四卡，trends 独立端点并行请求；不做 BFF 聚合端点
- [直方图对超大 run 的内存峰值] → run scope 行数 = items × metrics（百级），有界；SQL 侧不加 LIMIT

## Migration Plan

纯新增只读端点与前端页面，无 migration、无配置项。回滚 = 移除 router 注册与页面路由，无数据清理。

## Open Questions

无 — 探索阶段（竞品调研 + 范围讨论）已把范围线与口径全部钉死；延后项及触发条件见 proposal.md Non-goals 表。
