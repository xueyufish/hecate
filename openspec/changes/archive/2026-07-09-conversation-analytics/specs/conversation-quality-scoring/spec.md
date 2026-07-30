## ADDED Requirements — 新增需求

### Requirement: Turn-level quality scoring via LLM-as-Judge — 需求：通过 LLM-as-Judge 的轮级质量评分
系统应使用 LLM-as-Judge 评估已完成对话中的每个 assistant 轮次。评估者应评估三个维度：helpfulness（0.0–1.0）、coherence（0.0–1.0）和 instruction_adherence（0.0–1.0）。每个维度评分应包含解释评估的推理文本。评估提示应包含完整的对话上下文（所有之前的轮次）以实现准确评估。

#### Scenario: Score a single-turn conversation — 场景：评分的单轮对话
- **WHEN** 一个包含 1 条用户消息和 1 条 assistant 消息的对话完成
- **THEN** 系统为 assistant 轮次创建 1 条 ConversationTurnScoreModel 记录，包含 helpfulness、coherence、instruction_adherence 评分和推理

#### Scenario: Score a multi-turn conversation — 场景：评分多轮对话
- **WHEN** 一个包含 3 条用户消息和 3 条 assistant 消息的对话完成
- **THEN** 系统创建 3 条 ConversationTurnScoreModel 记录，每个 assistant 轮次一条，评分反映每轮在上下文中的质量

#### Scenario: LLM judge returns structured scores — 场景：LLM 评估者返回结构化评分
- **WHEN** LLM 评估者评估一个轮次
- **THEN** 响应包含 helpfulness（0.0–1.0）、coherence（0.0–1.0）、instruction_adherence（0.0–1.0）、topic（分类）和 reasoning（字符串）

### Requirement: Event-driven scoring trigger on conversation completion — 需求：对话完成时的事件驱动评分触发
系统应在对话状态更改为 "completed" 时异步触发质量评分。评分不应阻塞聊天响应路径。系统应使用 `asyncio.create_task()` 在后台运行评分。

#### Scenario: Conversation completes and scoring is triggered — 场景：对话完成并触发评分
- **WHEN** 对话状态更改为 "completed" 且 `CONVERSATION_QUALITY_SCORING_ENABLED` 为 True
- **THEN** 系统创建一个异步任务来对对话中的所有轮次进行评分

#### Scenario: Scoring is disabled via configuration — 场景：通过配置禁用评分
- **WHEN** `CONVERSATION_QUALITY_SCORING_ENABLED` 为 False
- **THEN** 对话完成时不创建评分任务

#### Scenario: Scoring does not block chat response — 场景：评分不阻塞聊天响应
- **WHEN** 对话完成并触发评分
- **THEN** 聊天响应立即返回给用户，无需等待评分完成

### Requirement: Configurable sampling rate — 需求：可配置的采样率
系统应支持可配置的采样率（`CONVERSATION_QUALITY_SAMPLING_RATE`，默认 1.0），控制完成对话中被评分的百分比。在 rate 1.0 时，所有对话都被评分。在 rate 0.1 时，只有 10% 被评分。

#### Scenario: Sampling rate 1.0 scores all conversations — 场景：采样率 1.0 评分所有对话
- **WHEN** 采样率为 1.0 且 10 个对话完成
- **THEN** 所有 10 个对话都被评分

#### Scenario: Sampling rate 0.5 scores half of conversations — 场景：采样率 0.5 评分一半对话
- **WHEN** 采样率为 0.5 且 10 个对话完成
- **THEN** 大约 5 个对话被评分（随机选择）

#### Scenario: Sampling rate 0.0 disables scoring — 场景：采样率 0.0 禁用评分
- **WHEN** 采样率为 0.0 且对话完成
- **THEN** 不创建评分任务

### Requirement: Conversation-level score aggregation — 需求：对话级评分聚合
系统应从轮级评分计算对话级聚合评分：`quality_score`（所有轮次 overall_scores 的平均值）、`quality_min_score`（最低轮次 overall_score）、`quality_metrics`（每个维度的平均值）和 `quality_scored_at`（时间戳）。这些聚合应存储在 ConversationModel 上。

#### Scenario: Aggregate scores computed from 3 turns — 场景：从 3 轮计算的聚合评分
- **WHEN** 一个对话有 3 轮，overall_scores 为 [0.9, 0.4, 0.8]
- **THEN** conversation quality_score 为 0.7（平均值），quality_min_score 为 0.4（最小值）

#### Scenario: Aggregate updated when new turn is scored — 场景：新轮次评分时更新聚合
- **WHEN** 新的轮次在已评分的对话中被评分
- **THEN** 会话聚合重新计算以包含新轮次

### Requirement: Configurable judge model — 需求：可配置的评估模型
系统应使用可配置的 LLM 模型进行质量评分（`CONVERSATION_QUALITY_JUDGE_MODEL`，默认 "gpt-4o-mini"）。系统应支持现有 LLM 服务中可用的任何 LLM 提供商。

#### Scenario: Default judge model — 场景：默认评估模型
- **WHEN** 未配置自定义评估模型
- **THEN** 系统使用 "gpt-4o-mini" 进行质量评分

#### Scenario: Custom judge model — 场景：自定义评估模型
- **WHEN** `CONVERSATION_QUALITY_JUDGE_MODEL` 设置为 "claude-3-haiku"
- **THEN** 系统使用 "claude-3-haiku" 进行质量评分

### Requirement: Quality scoring API endpoints — 需求：质量评分 API 端点
系统应暴露用于查询质量评分的 REST API 端点：`GET /api/ops-center/conversations/overview`（聚合指标）、`GET /api/ops-center/conversations/quality-distribution`（评分直方图）、`GET /api/ops-center/conversations/low-quality`（低于阈值的对话）、`GET /api/ops-center/conversations/{id}/turns`（每个对话的轮级评分）。

#### Scenario: Get conversation overview — 场景：获取对话概览
- **WHEN** 客户端请求 `GET /api/ops-center/conversations/overview?start_date=...&end_date=...`
- **THEN** 系统返回 `{total_conversations, scored_conversations, avg_quality_score, quality_distribution: {high, medium, low}}`

#### Scenario: Get quality distribution — 场景：获取质量分布
- **WHEN** 客户端请求 `GET /api/ops-center/conversations/quality-distribution?start_date=...&end_date=...`
- **THEN** 系统返回质量评分的直方图（桶：0.0–0.2、0.2–0.4、0.4–0.6、0.6–0.8、0.8–1.0）

#### Scenario: Get low-quality conversations — 场景：获取低质量对话
- **WHEN** 客户端请求 `GET /api/ops-center/conversations/low-quality?threshold=0.5&start_date=...&end_date=...`
- **THEN** 系统返回 quality_score 低于阈值的对话，按 quality_score 升序排列

#### Scenario: Get turn-level scores for a conversation — 场景：获取对话的轮级评分
- **WHEN** 客户端请求 `GET /api/ops-center/conversations/{id}/turns`
- **THEN** 系统返回该对话的所有轮次评分，按 turn_index 排序

### Requirement: Error handling for scoring failures — 需求：评分失败的错误处理
系统应优雅地处理 LLM 评分失败。如果某个轮次的评分失败，系统应记录错误并继续处理剩余轮次。失败的轮次不应有质量评分（可为空的列）。

#### Scenario: LLM call fails for one turn — 场景：一个轮次的 LLM 调用失败
- **WHEN** 在 3 轮对话中，第 2 轮的 LLM 调用失败
- **THEN** 系统记录错误，跳过第 2 轮，并继续对第 3 轮进行评分。第 2 轮没有质量评分。

#### Scenario: LLM call times out — 场景：LLM 调用超时
- **WHEN** LLM 调用超时（默认 30s）
- **THEN** 系统记录超时错误并跳过该轮次
