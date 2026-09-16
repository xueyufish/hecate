# Prompt Self-Optimization（6.19）

## Why

Agent 平台的 prompt 质量目前完全靠人工迭代：改模板 → 试跑 → 凭感觉判断。业界已经走完从"手工工程"到"评估数据集驱动的自动优化"这一步——Amazon Bedrock AdvPO 与 Google Vertex AI Prompt Optimizer 把"template + dataset + metric → 多轮自动重写"做成了标准产品契约，GEPA 成为算法事实标准（Google `adk optimize`、MLflow、Opik 均基于/集成）。对 Hecate 而言这是一个编排层变更而非地基变更：评估引擎（7.2c）、数据集命名版本（7.3b）、prompt 版本管理与对比分析（prompt-analytics）、`AgentDefinition.prompt_override` 覆盖缝隙（agent-as-tool 委托路径已消费）、1.3.6f 自进化闭环的房子模式（候选 → 门禁 → 人审 → 发布）全部已交付，只缺把它们串成闭环的优化器。

## What Changes

- 新增 **prompt 自优化闭环**（离线数据集驱动）：创建优化 run（钉定数据集版本 + trainset/valset 切分 + 评估器组 + 主指标 + 阈值 + 预算档位）→ 基线评估 → 多轮循环（真实 agent rollout + `prompt_override` 注入候选 → 逐 item 打分 → 聚合失败轨迹与 judge reasoning → 反思变异生成候选模板 → Jinja2 完整性门 → 门禁接受）→ 停止条件（预算耗尽 / 最大轮数 / 连续 N 轮无改进）→ 候选 + 证据报告。
- **策略可插拔**：`MutationStrategy` 裸名词 ABC（runtime 内部扩展点惯例）；v1 内置 GEPA 式反思变异策略（单主指标 + 其余指标不回退门禁，7.3a 哲学；Pareto-lite 候选池——top-k + 单项指标最优入池）。不引入 gepa/DSPy 依赖（设计 trade-off 见 design.md，重开触发条件已记录）。
- **runtime 接线（小加法）**：`agent_execute` 顶层路径消费 `agent_definition.prompt_override`——该字段与消费逻辑已在 agent-as-tool 委托路径存在（`agent_tool.py:170`），顶层目前仅消费 `tools`。
- **候选审核发布**：候选列表 + 证据报告（逐指标 delta、逐 item 失败明细、反思摘要、逐轮成本）API；候选内容安全扫描（复用既有 content-scanning/DLP 管线，防轨迹污染注入）；人审批准 → 发布为新 `PromptVersionModel`（自动生成 `commit_message` + provenance 元数据）；驳回留痕；回滚 = 既有版本/label 切换语义，无新机制。
- **预算与成本**：light/medium/heavy 三档预设 + 硬上限（最大轮数、最大 LLM 调用数）+ 逐轮成本记账（对齐 1.3.6f cost lineage 先例）；反思模型可独立路由（可与被测 agent 的模型不同）。
- **Flag 门控**：`PROMPT_OPTIMIZATION_ENABLED` 默认关，关闭时 studio 端点不可见。
- roadmap / feature-catalog 上 6.19 的完成状态改述延后至本 change 归档阶段处理。

## Capabilities

### New Capabilities

- `prompt-optimization-pipeline`: 自优化 run 的编排与执行——run 配置校验（prompt 存在、数据集版本钉定、评估器与主指标匹配、预算合法）、run 生命周期（created → running → awaiting_review / concluded）、基线评估、轮次执行（rollout override 注入、反思变异、Jinja2 完整性门、门禁接受）、预算强制与停止条件、逐轮成本记账、并发约束（同一 prompt 同时仅一个活动 run）、workspace 隔离。
- `prompt-optimization-review`: 候选验证后的审核与发布——候选列表与证据报告、内容安全扫描门、人审批准（发布为新版本 + provenance + 自动 commit_message）/ 驳回（留痕）、发布后回滚语义（复用版本与 label 切换）。

### Modified Capabilities

- `agent-invocation`: "EnginePort agent_execute method" requirement 扩展——顶层调用传入 `agent_definition.prompt_override` 时，系统提示词以覆盖值执行（此前顶层仅消费 `agent_definition.tools`；覆盖逻辑与 agent-as-tool 委托路径对齐）。

## Impact

- **代码**：新增 `ops/prompt_optimization/`（run 编排、策略与反思、门禁、runner——与 7.2c 的 `ops/evaluation/tasks/` 同族 job-runner 形态）；新增 `models/prompt_optimization.py`（optimization run / candidate 模型）+ alembic migration；修改 `runtime/agent_execution_port.py`（顶层 prompt_override 消费）与 `models/prompt.py`（版本 provenance 元数据列）；`core/composition/wiring.py` 注册。
- **API**：studio 新增优化 run 创建/列表/详情、候选列表/详情（证据报告）/批准/驳回端点。
- **依赖系统（只读消费，零改动）**：EvaluationEngine 与评估器组、数据集版本快照（7.3b）、PromptModel/PromptVersionModel、TraceModel（prompt_id/prompt_version 归因）、content-scanning/DLP、模型路由。
- **无新外部依赖**：反思与变异的 LLM 调用走平台既有模型路由；gepa/DSPy 明确不引入。
- **runtime 行为影响**：`agent_execute` 顶层对 `prompt_override` 的消费为加法语义（不传 `agent_definition` 时行为逐字不变，7.2c 的零改动承诺不回破）。
