# Proposal: automated-trace-backflow (7.2d)

## Why

7.2c 的在线评估任务已经持续对生产 trace 机评打分（`evaluation_task_scores`），但只产出分数、不写 `evaluation_items`——高质量的回归语料（尤其低分失败样本）没有回流到 evaluation dataset，数据集扩充完全依赖人工录入与 AI 合成。7.4 已交付**人工验证回流**（标注队列 completed item 一键物化），本 change 按收窄后的范围补齐另一半：**无人参与的自动化批量回流**。回流能力由"规则层 + 人工层"双层构成，规则层是当前缺口。同时本 change 承接 7.2c 延后记录中挂在 7.2d 上的 session 级多轮上下文（`EvalInput.conversation_history` 已备，仅需拉长投影开窗）。

## What Changes

- **Backflow 规则一等对象**：新增 `evaluation_backflow_rules`——`name`、`task_id`（source = 在线评估任务）、`dataset_id`（目标数据集）、metric 分数带 filter（`metric_name` + `min_score`/`max_score`，支持多条 AND 组合）、`limit`（单批物化上限，成本护栏）、可选 `max_turns`（多轮投影上限）。规则本身不自动触发。
- **批量触发 API**：`POST /api/evaluation/backflow-rules/{id}/run` 按规则筛选已评分 trace 并物化，返回 `{created, skipped, dataset_id}`（对齐 7.4 `push_to_dataset` 的返回形态）。规则的重复触发 = 再次调用 run 接口（可由既有 scheduled-tasks 能力编排），平台不新增 cron 基础设施。
- **自包含物化**：物化 item 携带 `query` / `generated_answer`（自包含拷贝，不依赖源 trace 存活）；trace 窗口内含多轮对话时保留 `conversation_history`；`expected_answer` 留空——**机分是溯源（provenance），不是 ground truth**。
- **溯源与去重**：`metadata_.backflow` 记录 `trace_id` / `task_id` / `rule_id` / 命中的 scores 快照；`tags=["trace-backflow", rule.name]`；幂等去重与 7.4 标注路径打通——同一条 trace 不能经任一路径重复进入同一 dataset。
- **非目标（Non-goals）**：
  - 平台内置 cron / online worker 内联钩子（v1.5；对齐 7.2c 对周期调度的延后决策——重复触发 = 再次调 run 接口，编排交给既有 scheduled-tasks）
  - 历史 trace backfill（v1.5 候选）
  - 机分自动转写 `expected_answer` / golden 标签
  - 源 trace 的 retention pin（物化 item 自包含，溯源链接断裂只影响 UI 下钻；v1.5 评估）
  - known-bad 豁免 / dataset 命名版本 / publish 门禁（兄弟特性 7.3c / 7.3b / 7.3a，独立 change）

## Capabilities

### New Capabilities
- `trace-backflow`: 自动化回流能力——backflow 规则 CRUD、按规则批量物化已评分 trace 到 dataset、跨路径幂等去重、多轮对话投影、机分溯源记录

### Modified Capabilities

（无——`evaluation-tasks` / `evaluation-dataset` / `human-annotation` 的既有 requirement 均不变；规则通过 `task_id`/`dataset_id` 引用既有资源，不改变它们自身的行为契约）

## Impact

- **数据模型**：`src/hecate/models/evaluation.py` 新增 `EvaluationBackflowRuleModel` + Create/Update/Read schemas；alembic migration（新表，无既有表变更）
- **服务层**：`src/hecate/ops/evaluation/` 新增 `backflow/` 子模块（service）；复用 `tasks/trace_input.build_eval_input`（拉长开窗支持多轮）与 7.4 的 dataset 解析/去重形态
- **API 层**：`ops/api` evaluation 路由新增 backflow-rules CRUD + run 端点
- **既有代码小改**：`annotation/service.py` 的 `_dataset_trace_ids` 去重读取需同时识别 `metadata_.backflow.trace_id`（跨路径幂等）；注释更新
- **零影响面**：runtime 请求路径、在线评分 worker 主流程、既有 API 契约均不变；无 BREAKING 变更
