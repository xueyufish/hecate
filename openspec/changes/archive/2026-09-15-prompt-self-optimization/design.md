# Design — Prompt Self-Optimization（6.19）

## Context

底座全部已交付（见 proposal → Why）：EvaluationEngine（确定性 ∥ + LLM-judge，`Score.reasoning` 承载自然语言判定理由）、数据集命名版本（7.3b hash 快照）、`PromptModel`/`PromptVersionModel`（不可变版本、labels、commit_message）、prompt-analytics（per-version 指标与 compare API）、`AgentDefinition.prompt_override`（`runtime/agent_tool.py:47`，委托路径已消费）与 `agent_execute` 的 `agent_definition` 参数缝隙（`runtime/ports.py:260`，顶层目前仅消费 `tools`，见 `runtime/agent_execution_port.py:117`）、1.3.6f 的闭环房子模式（候选 → 门禁 → 人审 → 发布、flag 门控、cost lineage）。7.2c 的 `ops/evaluation/tasks/runner.py` 提供了同族 job-runner 形态（后台执行 + 状态轮询 + `items_override` 先例）。业界对照与算法细节见 `docs/research/2026-09-prompt-optimization-practices-survey.md`。

## Goals / Non-Goals

**Goals:**

- 离线数据集驱动的多轮 prompt 自优化闭环，frozen-weight，候选绝不自动发布。
- 真实 agent rollout 保真度（prompt_override 注入），超过 Bedrock/Vertex 的"目标模型直跑模板"形态。
- 策略可插拔 + 预算可控 + 全程成本记账，多租户下可安全开放。
- 发布物即普通 prompt 版本：provenance 可溯、复用既有 diff/analytics/label/回滚语义。

**Non-Goals:**

- 在线流量驱动优化与自动 A/B 切换（业界 v1 均缺席；在线信号留后续）。
- 多模板批量优化 job（Bedrock 10 templates/job 形态，留 v2）。
- 分区级选择性优化标签（Bedrock `{{advpo:optimize}}` 式；v1 全模板变异 + 变量完整性门）。
- 工具描述优化（Anthropic "optimize tools, not prompts" 方向）、拓扑级优化（ADAS/AFlow）、approved_with_edits（编辑走 prompt 原生编辑流，发布后正常改版）。
- 引入 gepa/DSPy 运行时依赖（D1）。

## Decisions

### D1 — 自研 harness + `MutationStrategy` ABC；gepa 不引入

v1 循环自研（sample → rollout → reflect → mutate → gate），策略面收敛为一个裸名词 ABC（`MutationStrategy`：输入基线模板 + 轮次证据，输出候选模板与反思摘要），完全符合 runtime 内部扩展点惯例与 1.3.6f 先例。

**否决的备选**：以 `gepa.optimize(...)` 为引擎。理由：① 循环控制权——gepa 主控循环并假设 sync callable 的 `task_lm`/metric，而 Hecate 的 rollout 是异步、tenant 隔离、穿 RuntimePort 的 `agent_execute`，metric 是 EvaluationEngine 整套评估器组，同步/异步桥接与候选表示映射（gepa candidate = component→instruction 字典，为 DSPy 程序设计）成本不低于自研循环本身；② 双重记账——gepa 的 track_stats/log_dir 与平台 run/lineage/成本账需要对齐；③ v1 搜索需求（单目标、单组件）用不上 GEPA 的重装备（Pareto 全量选择、merge 算子），而 GEPA 真正关键的"完整 trace 作反思信号"一半已免费存在（`Score.reasoning` + 轨迹）；④ 快速漂移的研究库依赖对自托管企业平台是长期支持负担。promptfoo（朴素循环）与 Braintrust Loop（LLM 线程）证明该量级下反思质量比搜索机制更决定效果。

**重开触发条件**（记录在案，防止永久化）：多目标成为真实需求，或单目标循环实测平台期 → 以 optional extra 引入 `GepaStrategy` 适配器，接入面已被 ABC 收敛为一个类。

### D2 — 门禁：单主指标 + 不回退；选择：Pareto-lite

**选择策略与接受门禁分层**。接受门禁（可发版与否）永远是二值且可解释的：主指标提升 ≥ `min_improvement` ∧ 确定性指标回退 ≤ `max_regression`——复用 7.3a publish gate 哲学（确定性硬门，judge 软门：非主指标 judge 回退只上报不拦截；主指标为 judge 时 δ 余量吸收噪声）。显式拒绝加权合成分（量纲不可通约、delta 不可解释）。

池内选择 v1 取 Pareto-lite：current_best + top-k + 单项指标最优入池（逐 item 每指标分数本来就有，几乎零成本）。v2 若升级 GEPA objective Pareto（`frontier_type="objective"` 同款语义），是**换一个选择策略**而非改数据模型——前提是逐 item 每指标分数从第一天全量落库。

### D3 — rollout 保真度：真实 agent + prompt_override 顶层接线

候选打分 = 以 `agent_definition.prompt_override` 调 `agent_execute` 跑真实 agent（工具/知识库/模型配置不变）。runtime 侧改动为加法：顶层路径消费 `prompt_override`（委托路径已有同款逻辑），不传 `agent_definition` 时行为逐字不变，7.2c 的零改动承诺不回破。

**否决的备选**：① judge-only 直评（`EvalInput.system_prompt` 构造输入）——量的是"prompt 对 judge"而非 agent 真实行为，保真度甚至低于 Bedrock；② 每候选建草稿 agent 版本（1.3.20）——每轮每候选一个版本快照太重且产生清理噪音。

### D4 — 位置与形态：`ops/prompt_optimization/`，job-runner 与 7.2c 同族

优化 run 是数据集驱动的批任务，不是 studio 会话型工作流：与 `ops/evaluation/tasks/` 同族（后台 runner + 状态轮询），执行载体复用该模式。studio 侧只放 API surface（run 创建/查询、候选审核发布）。不并入 `studio/self_evolution/`：优化对象（prompt 版本 vs skill 包）、算法内核（反思变异 vs AgentRx 归因）、门禁（评估门禁 vs 四件套门禁）均不同——共享的是"候选 → 门禁 → 人审 → 发布"模式，不是代码。

### D5 — 离线驱动 + 钉定 + 切分

创建时钉定数据集版本（7.3b hash），train/val 按 `split_ratio`（默认 0.8/0.2，promptfoo 惯例）切分：train 供轮内 minibatch rollout，val 供门禁评估。dataset 与 evaluator 配置进 run 快照，运行后变更不影响进行中/已完成 run。

### D6 — 反思变异与模型路由

**Rollout 语义**：`agent_id` 是 run 配置的一部分（被测 agent = rollout 载体）。baseline 与所有候选都以 `prompt_override=模板` 注入同一 agent 执行——delta 度量的是"模板 vs 模板"，被测 agent 的 persona 在 run 期间被系统性旁路，工具/知识库/模型配置保持其生产配置。轮次证据聚合：本轮 rollout 的逐 item 失败（query、expected、generated、evaluator `reasoning`）+ 上轮门禁报告 → 反思 LLM 生成候选模板 + 反思摘要。反思模型默认与被测 agent 的模型路由一致，允许 run 级 override（GEPA `reflection_lm` 可强于 `task_lm` 的同款思路，企业租户可按预算选择）。rollout 的 trace metadata 带 run id + candidate id，与生产流量可分离，不污染 prompt-analytics 归因。

### D7 — 模板完整性门

任何候选在 rollout 前必须通过：模板引擎 parse 成功 ∧ 提取变量集合 == 基线模板声明变量集合。失败候选记为 `rejected_mutation`（含原因），不耗 rollout 预算。v1 全模板变异；分区级选择性优化（Bedrock `{{advpo:optimize}}`/`{{advpo:exclude}}` 式，天然与 ACE 结构化 delta 配套）留 v2。

### D8 — 预算与停止

`light`/`medium`/`heavy` 三档预设映射到 caps，run 配置可显式覆盖。任一 cap 触顶 → 停止，stop_reason=`budget_exhausted`。停止条件族：预算、`max_rounds`、连续 N 轮（默认 3）无过门候选（`no_progress`）、用户取消。逐轮 usage（调用数、tokens、时长、成本）落库并在 run 上累加——对齐 1.3.6f cost lineage。

计数口径：rollout item 计 `agent_execute` 次数（train minibatch + val 门禁评估合计）；`mutation_calls` 计反思 LLM 调用次数；评估器侧 LLM-judge 开销属 7.x 评估配额，不重复计入。

预设数值（对齐 GEPA"100–500 次评估见显著提升"的经验区间；flag 默认关，内部试运行后仅调常量）：

| 档位 | max_rounds | mutation_calls | rollout_items |
|---|---|---|---|
| light | 2 | 10 | 100 |
| medium | 5 | 25 | 400 |
| heavy | 10 | 50 | 1000 |

### D9 — 发布与 provenance

批准候选 → 新建 `PromptVersionModel`（版本号顺延）：`commit_message` 自动生成（引用 run + 主指标 delta），provenance 写入 `PromptVersionModel` 新增的 nullable `metadata_` JSONB 列（平台既有 `metadata_` 惯例；含 run id、candidate id、基线/候选分数）。**不自动打任何 label**（production 受保护标签的既有 RBAC 不变）；回滚 = 既有版本/label 切换语义，零新机制。

### D10 — 安全门

过门候选进人审池前过既有 content-scanning/DLP 管线（1.3.6f 同款，防轨迹污染式 prompt 注入）；blocked 候选 `scan_blocked` 不可批准、留痕可查。rollout 对生产配置只读（D3）是第二道 taint 隔离。

### D11 — 数据模型（migration 草图）

- `prompt_optimization_runs`：id、workspace_id、prompt_id、base_version、dataset_id、dataset_version（hash）、split_ratio、evaluators_config（JSON）、primary_metric、min_improvement、max_regression、budget_preset、max_rounds、max_llm_calls、strategy、strategy_params（JSON）、reflection_model、status、stop_reason、usage（JSON）、created_by、时间戳。
- `prompt_optimization_candidates`：id、run_id、round_no、parent_candidate_id（lineage）、template、status（`rejected_mutation` / `gate_rejected` / `scan_blocked` / `pending_review` / `published` / `rejected`）、gate_report（JSON）、per_item_results（JSON）、reflection_summary、usage（JSON）、rejection_reason、decided_by / decided_at。
- `prompt_versions` 加 `metadata_` JSONB nullable 列（D9）。

### D12 — API surface（studio）

`POST /api/prompt-optimization/runs`、`GET /api/prompt-optimization/runs[/{id}]`、`POST .../runs/{id}/cancel`、`GET .../runs/{id}/candidates[/{id}]`、`POST .../candidates/{id}/approve`、`POST .../candidates/{id}/reject`。flag 关闭时全部 404。

## Risks / Trade-offs

- **judge 噪声使门禁不稳** → 确定性指标硬门 + 主指标 δ 余量 + 单指标多次 rollout（评估器配置既有 repetitions 语义）；文档如实披露 judge 精度预期（AgentArts 公开"最高 70%"是行业诚实例）。
- **反思质量依赖强模型** → reflection model override + 成本记账可见；若租户用弱模型效果差属配置问题，证据报告可见。
- **单指标平台期**（主指标提升常以牺牲他指标为代价，被门禁拒绝） → `no_progress` 停止止损；这是 D2 门禁语义下的**安全失败**（不发布而已），v2 Pareto 选择策略是解法。
- **长时 run 的进程重启** → 与 7.2c 相同语义：run 标 `failed`、证据保留、另起新 run；v1 不做断点续跑（成本可控前提下接受，Bedrock 同为 15 分钟～数小时量级）。
- **rollout 烧钱** → 三层防线：完整性门挡无效变异（不耗预算）、硬 caps、逐轮记账。
- **优化 trace 混入生产分析** → metadata 打标（D6）；prompt-analytics 的 UI 过滤入口后补（数据层已可分离）。
- **候选模板注入指令**（数据集被污染时反思可能放大） → content-scan 门 + 人审双关；数据集来源治理属 7.x 既有范畴。

## Migration Plan

1. Migration：两张新表 + `prompt_versions.metadata_` 列，全部加法、向后兼容。
2. `PROMPT_OPTIMIZATION_ENABLED` 默认关：合入后生产行为零变化。
3. 开启顺序：先内部 workspace 试运行（用 7.2b 合成数据集），确认成本账与门禁行为后再对外开放端点。
4. 回滚：关 flag 即封锁全部入口；表与列为惰性数据，无需回滚 migration。

## Open Questions

以下均为 2026-09-15 与用户逐项确认过处置的延后项，不阻塞实现：

- prompt-analytics 是否需要显式的"排除优化流量"开关——数据层已带 run 标记，UI 过滤待真实使用反馈。
- 轮内 minibatch 采样策略（随机 vs 难例优先）——v1 随机，实测后评估难例加权是否值得。
- 失败候选是否参与后续轮次的 merge（GEPA `use_merge` 式候选合并）——v2 策略层议题。
