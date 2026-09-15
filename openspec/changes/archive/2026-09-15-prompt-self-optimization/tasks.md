## 1. 数据模型与迁移

- [x] 1.1 新增 `models/prompt_optimization.py`：`PromptOptimizationRunModel`（run 配置快照、status、stop_reason、usage）与 `PromptOptimizationCandidateModel`（lineage、template、status、gate_report、per_item_results、reflection_summary、usage、decision 字段），含 Pydantic Create/Read schemas，按 design D11
- [x] 1.2 `models/prompt.py`：`PromptVersionModel` 加 nullable `metadata_` JSONB 列（provenance 载体）+ schema 透出
- [x] 1.3 Alembic migration：两张新表 + `prompt_versions.metadata_` 列，workspace 索引齐全，downgrade 完整
- [x] 1.4 Feature flag：注册 `PROMPT_OPTIMIZATION_ENABLED`（默认 off）

## 2. Runtime 接线（prompt_override 顶层消费）

- [x] 2.1 `runtime/agent_execution_port.py`：顶层 `agent_execute` 消费 `agent_definition.prompt_override` 覆盖 system prompt（对齐 `agent_tool.py` 委托路径语义；不传时行为不变）
- [x] 2.2 测试：prompt_override 生效、未传时逐字回归、override 下 tools/KB/model/hooks 仍走 agent 配置

## 3. 优化管线核心（ops/prompt_optimization/）

- [x] 3.1 包骨架：`ops/prompt_optimization/`（runner、service、gates、strategy、scanner、review）；实现期为 API 触发的 job-runner（与 7.2c 同族，design D4），无需 composition root 的 scheduler 注册；flag 关闭时端点与执行全不可达
- [x] 3.2 Run service：创建校验（prompt/agent/dataset version 存在性、primary_metric ∈ 评估器产出、split 合法、同 prompt 单活动 run → 409）、配置快照落库、取消
- [x] 3.3 后台 runner（7.2c job-runner 同族）：生命周期状态机 `created → running → awaiting_review / concluded / failed`，内部失败标 failed 且保留证据
- [x] 3.4 基线评估：val split 全量评估器打分并落库；基线失败 → run failed
- [x] 3.5 Rollout 执行：train minibatch / val 门禁两路评估，`agent_execute` + `agent_definition.prompt_override` 注入候选，trace metadata 带 run id + candidate id
- [x] 3.6 `MutationStrategy` ABC + v1 反思变异实现：聚合逐 item 失败（query/expected/generated/`Score.reasoning`）→ 反思 LLM 出候选模板 + 反思摘要；reflection model 支持 run 级 override
- [x] 3.7 模板完整性门：parse + 变量集合 == 基线声明集合；失败记 `rejected_mutation` 不耗 rollout 预算
- [x] 3.8 接受门禁：主指标 ≥ baseline + min_improvement ∧ 确定性指标回退 ≤ max_regression（judge 非主指标只上报）；gate_report（逐指标 delta、逐 check pass/fail）全量落库
- [x] 3.9 Pareto-lite 候选池：current_best + top-k + 单项最优入池
- [x] 3.10 预算与停止：light/medium/heavy 预设 caps（数值见 design D8）+ 显式覆盖；budget_exhausted / max_rounds / no_progress（默认 3）/ cancelled；逐轮 usage 记账（调用数、tokens、时长、成本）并在 run 累加

## 4. 审核与发布

- [x] 4.1 内容安全门：gate-passing 候选接 content-scanning/DLP 管线，blocked → `scan_blocked` 留痕不可批准
- [x] 4.2 证据报告 API：候选详情含模板 diff、逐指标 delta、gate report、逐 item 失败明细、反思摘要、lineage、成本；gate_rejected 候选可见不可审
- [x] 4.3 批准发布：新建 `PromptVersionModel`（自动 commit_message 引用 run + 指标 delta；`metadata_` 写 run id / candidate id / 分数；不自动打 label）
- [x] 4.4 驳回：reason 必填 + reviewer/时间戳落库；池内全部候选有终态 → run `concluded`
- [x] 4.5 审核防线测试：无批准不产生版本、scan_blocked 不可批准、驳回候选可查、发布版本与手工版本行为一致（diff/analytics/label）

## 5. Studio API 与 flag 集成

- [x] 5.1 端点族：`POST/GET /api/prompt-optimization/runs`、`GET runs/{id}`、`POST runs/{id}/cancel`、`GET runs/{id}/candidates[/{id}]`、`POST candidates/{id}/approve|reject`（workspace 隔离 + flag 404）
- [x] 5.2 权限与租户：workspace scoping 全链路、跨 workspace 引用 404

## 6. 验证与收尾

- [x] 6.1 单测覆盖 spec 场景：pipeline（创建校验/生命周期/基线/完整性门/接受门禁/预算停止/钉定复现/workspace）+ review（证据/扫描/批准发布/驳回/无自动发布）+ agent-invocation prompt_override 三态
- [x] 6.2 端到端集成测试：合成数据集 + stub agent 跑通 created → rounds → awaiting_review → approve → 新版本带 provenance
- [x] 6.3 四件套全绿：`ruff check`、`ruff format --check`、`mypy src/`、`python -m pytest tests/ -q`
- [x] 6.4 `.env.example` / 部署文档补 `PROMPT_OPTIMIZATION_ENABLED` 说明（默认 off + 开启前提：具备 7.x 评估数据集）
