# Proposal: workflow-evaluation (7.3)

## Why

评估体系已具备 agent 级（`answer_source=agent`）和 LLM 调用级（`answer_source=pipeline`）的"按条生成答案 + 评估器打分"闭环（7.2c，2026-09-11），但 **workflow 作为被测对象没有落点**：`EvaluationTask.AnswerSource` 枚举里没有 `workflow`；7.2c 落地时显式把"工作流级评估"和"session 级多轮"推给了后续特性（见 `openspec/changes/archive/2026-09-11-online-offline-evaluation-tasks/proposal.md` 的 Non-goals 与 `tasks/runner.py` 已锁定的扩展点）。工作流版本（1.1.9 ✅，`WorkflowVersionModel` immutable、`published_version` 可发布）和评估回归（7.2c，`baseline_run_id` + `regression_threshold`）也始终没接上——同一份数据集跨版本的 diff、publish 时的质量报告、CI 阻断门禁都缺。

业界对比研究（`docs/research/2026-09-workflow-evaluation-competitive-analysis.md` 计划新增）：AWS Bedrock AgentCore Evaluations 把 dataset-runner 与 batch-runner 明确分档、Google ADK 的 conformance testing 以录-放-比阻断 merge、Salesforce Agentforce Testing Center 把 run history 与 CI 部署阻断做成产品形态、Palantir AIP Evals 以 Suite/Target/Eval-function/Metric 四件套覆盖多 target 并行对照、LangSmith/Langfuse/Braintrust 普遍建立 dataset×experiment 快照。中端产品（华为 AgentArts、OpenJiuwen、Dify、Manus、美团 CatPaw）几乎全部空白，Dify 用户长期呼声 app 级评测而官方始终未做——这是 7.3 在中文 Agent 平台的差异化锚点。

补齐 7.3 让平台第一次具备"端到端工作流跑评估集 + 跨版本回归对比 + CI 可消费门禁"的完整链路，是 7.9（Testing Center）、7.5（A/B Testing agent-level）、8.10（CI/CD 评估门禁）和 7.2e（评估报表）的共同前置。

## What Changes

- **新增 `AnswerSource.WORKFLOW`**：`EvaluationTask` 的 `answer_source` 枚举扩 `workflow`；任务配置追加 `workflow_id`（必填）、`workflow_version`（可选，缺省取该 workflow 的当前最新版本，运行启动时解析锁定）、`repetitions`（int，默认 1）。复用 7.2c 既有离线任务异步生命周期（`pending → running → completed | failed`），不引入新 run 状态机。
- **执行路径改造**：`EvaluationEngine._generate_answer_*` 体系新增 `_generate_answer_via_workflow` 分支，按 dataset 条目非流式调用 `WorkflowExecutionService.execute`（懒导入 `hecate.studio.workflows.execution_service`，沿用 `ops→studio` 既有跨域规则）。每条目新建一次性 session，timeout 与现有 chat API 路径一致；轨迹（节点 id / 节点类型 / 状态 / 耗时 / 错误）落入 run 载荷（`node_results`），节点级打分不在 7.3 范围内（数据就位留待 7.9 / 7.2e 复用）。
- **回归数学升级**：`EvaluationRunModel` 增加可空 `workflow_id`、`workflow_version`（与现有 `task_id`/`summary` 一并扩展，Alembic 增量迁移，向后兼容）。`summary` 在 `repetitions > 1` 时产出 `pass_rate`（任一次过）与 `consistency_rate`（每次都过），并把 7.2c 的 `baseline_run_id` 升级为"基线 run 关联的 workflow_version + baseline drift 摘要）。`baseline_run_id` 引用同一工作流同一数据集但不同版本时，自动在 diff 响应里标"数据集漂移"（见下一条）。
- **dataset 快照冻结（run 时拷贝）**：任务启动时把数据集条目 JSON + 内容 hash 落进 run 记录（`dataset_snapshot` 列）；跨 run 比较若 hash 不一致，diff 响应里给出 `dataset_drift: {old_hash, new_hash, changed_item_ids}`，不阻断计算但明确标注。**不引入命名 dataset 版本对象**（即 v3.0 字符串版本、版本目录树等）——只满足"两次 run 用的是否同一份数据"的健全性需求，差异化于 Langfuse（其文档明文"experiment always runs on latest dataset version"，不支持 pinned 重放）。
- **新增 diff API**：`POST /api/evaluation/runs/compare`（在 `regression-testing` 已声明接口上落实工作流版本场景）接受 `baseline_run_id`、`candidate_run_id`，响应新增 `workflow_version`、配对 delta（`{metric: {baseline_avg, candidate_avg, delta, is_regression}}`）、`token_usage_delta`、`latency_delta`、`cost_delta`、`dataset_drift`、`node_drift`（两边都跑了同一节点集且节点数量一致时给出每节点耗时差）。`is_regression` 判定沿用 7.2c `regression_threshold`（默认 5%）。
- **publish 建议式评估报告**：在 `POST /api/evaluation/workflow-evaluations/{workflow_version}/runs` 触发的 run 与已发布版本的最近 run 配对自动算 diff，结果作为可选字段回填 `POST /workflows/{workflow_id}/publish/{version}` 的响应（`evaluation_report` 字段，不阻断发布），让用户在 publish API 里直接看到回归摘要。**v1 不引入 publish 阻断开关**——保留 7.3a 子特性位（关联到 publish API 的契约变更应独立评审）。
- **成本护栏（v1 最小集）**：单 run `items × repetitions` 总执行数上限（默认 1000，任务可调）；单 run 内并发上限（默认 4，任务可调）。超阈值拒绝触发并附 400 错误，避免不知情的成本爆炸。
- **CLI 入口**：在 `src/hecate/cli/commands/workflow.py` 新增 `eval run` 子命令，参数 `--dataset`/`--workflow-version`/`--repetitions`/`--baseline-run-id`，输出 JSON 结果；退出码三态：0 通过、2 数据集漂移告警、3 评分低于阈值。**不内置 PR comment 渲染**——按 Braintrust action 同款设计，把"报告渲染"和"门禁判定"分开。
- **不改动**：既有 `evaluation_runs` 同步接口、`runs/compare`、`regression/run`、`evaluation_tasks` 全部 task/runs 行为（列增量、语义保持）；runtime 请求路径零改动；现有 `_TestWorker`（`studio/workflows/test_runner.py` 的 mock-only 路径）原样保留，**不**把"结构化冒烟测试"和"质量评估"两件事混进同一个出口。
- **不进 7.3（记 deferred，留后续子特性位）**：
  - **7.3a** — publish 阻断开关（仅确定性评估器可参与阻断判定，默认关闭）。独立 change，关联 1.1.9 publish API 契约变更。
  - **7.3b** — 命名 dataset 版本管理（版本对象 + checkout / diff UI）。先让 v1 的快照冻结机制跑稳，看真实需求方是否自然形成。
  - **7.3c** — known-bad 豁免标注（豁免对 baseline 数学的影响需独立 design）。先走"移出数据集"的 workaround。
  - **7.2e 承接** — 跨版本 diff 视图 UI（dashboard）。7.3 只交 API 地基。
  - **7.2d/7.2e 承接** — session 级多轮评估、user simulation / persona。
  - **不编号 deferred** — conformance 录-放-比（golden recording vs replay）。Hecate 已有 EventStore + time-travel 底子，价值高但工程量大，独立 PR。
- **BREAKING**：无。

## Capabilities

### New Capabilities

- `workflow-evaluation`: 端到端工作流评估——`AnswerSource=workflow` 路径、repetitions、dataset 快照冻结、workflow-version 绑定 run、配对 delta diff（指标 / tokens / latency / cost + 数据集漂移 + 节点漂移）、publish 建议式报告、CLI eval run + 三态退出码、单 run 成本护栏。

### Modified Capabilities

（无。`regression-testing` 已声明的"Run comparison API"/"Regression trigger API"需求在 v1 由本 change 真正实现，但**requirement 级语义未变**——不修改 capability，留待真正的产品差异出现时再处理。`evaluation-tasks` 与 `evaluation-api` 同理。）

## Impact

- **数据模型/迁移**：`src/hecate/models/evaluation.py` 的 `EvaluationRunModel` 增加可空 `workflow_id`、`workflow_version`、`dataset_snapshot`、`repetitions`；`EvaluationTaskModel` 增加 `workflow_id`/`workflow_version`/`repetitions` 字段解析路径；一个 alembic 增量迁移（既有 200+ 列全部 nullable，向后兼容）。
- **评估域代码**：`src/hecate/ops/evaluation/types.py` 给 `AnswerSource` 枚举加 `WORKFLOW`；`src/hecate/ops/evaluation/engine.py` 新增 `_generate_answer_via_workflow` 与 `_summarize_repetitions`（repetitions 聚合 + drift 计算）；`src/hecate/ops/evaluation/tasks/service.py` 扩展 `build_task_config` 接受新字段、`validate_task` 校验 workflow_id 存在与版本可解析；`src/hecate/ops/evaluation/tasks/runner.py` 把 workflow 分支串到既有 lifecycle。
- **Studio 域函数级懒导入**：`src/hecate/ops/evaluation/engine.py` 在新分支里按 `tests/test_layering_domain.py` 既定规则懒导入 `hecate.studio.workflows.execution_service.WorkflowExecutionService`（参考既有 `ops/scheduling/executors.py → studio.workflows.test_runner` 模式）。
- **API**：`src/hecate/ops/api/evaluation.py` 或新 router 增加 `/api/evaluation/workflow-evaluations/{workflow_version}/runs`、`/api/evaluation/runs/compare`（承接 regression-testing 已声明接口）；`src/hecate/studio/api/workflows.py` 的 `POST /workflows/{workflow_id}/publish/{version}` 响应里附 `evaluation_report`（service 层组装，API 层不动路径语义）。
- **CLI**：`src/hecate/cli/commands/workflow.py` 增加 `eval run` 子命令 + 退出码逻辑。
- **组合根**：无需新增启动任务；离线 run 复用 7.2c 的 `OfflineTaskRunner.run_in_background` 生命周期。
- **依赖**：无新三方包；仅复用既有（SQLAlchemy、PluginRegistry、WorkflowExecutionService、RuntimePort）。
- **下游铺路**：为 7.9（Testing Center 节点级打分读 node_results）、7.5 agent-level（A/B 实验复现基线）、7.2e（diff API 作为 dashboard 数据源）、8.10（CI job 消费 CLI 退出码）提供底座。