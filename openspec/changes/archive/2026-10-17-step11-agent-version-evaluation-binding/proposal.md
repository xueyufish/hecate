# Proposal

## Why

Agent 发布门禁通过可修改的 `evaluation_tasks.config.agent_id` 寻找最近完成的评测，无法证明它评测了待发布版本。旧版本高分可能放行新版本，修改任务还能把旧 Run 的归属转给另一个 Agent。本 change 交付演进方案 step11 的一个最小切片：评测证据与已提交 AgentVersion 绑定。

## What Changes

1. 离线 `answer_source=agent` 任务支持声明 `agent_version`。Run 开始时解析已提交版本，保存自身的 `agent_id`、`agent_version`、`agent_config_hash` 与执行快照（配置及资源引用）。历史 Run 保持未绑定，不回填。
2. 版本绑定评测实际生成答案，不复用数据集预存答案。执行适配器使用快照 persona、模型参数、工具名单、技能名单及 pin、知识库引用；workflow/three_layer 模式复用现有 EntryExecutionService，workflow 使用冻结的 workflow 版本。解析、租户校验或调用失败显式失败，不退回草稿。
3. 发布仅消费与 Agent、版本、配置哈希及 workspace 匹配的已完成 Run。任务之后修改或删除不影响证据归属。require 无匹配证据必拒绝，baseline 取当前 published_version 的匹配 Run；启用回归但无基线时拒绝。漂移沿用共享评分逻辑，传入当前数据集哈希，缺少必要快照时拒绝。
4. warn 返回失败报告但不阻断；force 必须先写入操作者和门禁结果审计，报告标为 bypass，不能标为通过。审计失败不移动发布指针。
5. 增量迁移接到现有唯一 head。无数据回填；仅在没有绑定证据时允许删列降级，有证据时拒绝破坏性 downgrade。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent-versioning`：按不可变 Run 绑定匹配候选、基线与发布报告。
- `evaluation-tasks`：声明版本、冻结运行身份及按快照实际执行。

## Impact

涉及 evaluation 模型/迁移、任务配置与 runner、评测引擎、Agent versioning、执行适配器和 SkillLoader；workflow 入口复用时按版本技能名单加载。`POST /runs` 现行不提供 agent 答题来源，本 change 不扩展该 API。

使用 full spec-driven：涉及持久化契约、运行语义、跨域装配和租户校验。边界内不新增 RuntimePort、执行驱动或 durable 契约，不修改共享 publish_gate.py 的评分算法，不建立部署激活、签名、灰度或发布中心。技能治理/发现池、未 pin 内容、工具实现及知识库内容继续遵守已有 live 解析契约；本切片不声明完整部署组合可复现。
