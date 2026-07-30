## ADDED Requirements — 新增需求

### Requirement: Turn-level user feedback capture — 需求：轮级用户反馈捕获
系统应允许用户对单个 assistant 轮次进行 positive 或 negative 评分，并附带可选评论。反馈应存储在 ConversationTurnScoreModel 中，与自动质量评分并列。

#### Scenario: Submit positive feedback for a turn — 场景：为轮次提交正面反馈
- **WHEN** 用户为对话的第 3 轮提交反馈 `{rating: "positive", comment: "Great answer!"}`
- **THEN** 系统使用 user_rating="positive"、user_comment="Great answer!"、feedback_at=current_timestamp 更新第 3 轮的 ConversationTurnScoreModel 记录

#### Scenario: Submit negative feedback without comment — 场景：提交无评论的负面反馈
- **WHEN** 用户为第 5 轮提交反馈 `{rating: "negative"}`
- **THEN** 系统使用 user_rating="negative"、user_comment=null 更新第 5 轮的 ConversationTurnScoreModel 记录

#### Scenario: Overwrite existing feedback — 场景：覆盖现有反馈
- **WHEN** 用户为已有反馈的轮次提交反馈
- **THEN** 系统用新的评分和评论覆盖之前的反馈

### Requirement: Feedback API endpoint — 需求：反馈 API 端点
系统应暴露 `POST /api/ops-center/conversations/{id}/turns/{turn_index}/feedback` 用于提交轮级反馈。端点应接受 `{rating: "positive"|"negative", comment: str | None}` 并返回更新后的轮次评分记录。

#### Scenario: Submit feedback via API — 场景：通过 API 提交反馈
- **WHEN** 客户端发送 `POST /api/ops-center/conversations/{id}/turns/3/feedback`，请求体为 `{rating: "positive", comment: "Helpful"}`
- **THEN** 系统返回 200 及更新后的 ConversationTurnScoreModel 记录

#### Scenario: Invalid rating value — 场景：无效的评分值
- **WHEN** 客户端发送 feedback，rating 为 `"neutral"`（不是 positive/negative）
- **THEN** 系统返回 422 及验证错误

#### Scenario: Turn not found — 场景：轮次未找到
- **WHEN** 客户端为不存在的 turn_index 提交反馈
- **THEN** 系统返回 404 及错误消息

### Requirement: Conversation-level feedback summary — 需求：对话级反馈摘要
系统应在 ConversationModel 上计算反馈摘要，聚合所有轮级反馈：`{positive: count, negative: count, total: count}`。此摘要应在提交反馈时更新。

#### Scenario: Feedback summary after 3 feedback submissions — 场景：3 次反馈提交后的反馈摘要
- **WHEN** 一个对话有 2 条正面和 1 条负面反馈提交
- **THEN** conversation.feedback_summary 为 `{positive: 2, negative: 1, total: 3}`

#### Scenario: Feedback summary updated on new submission — 场景：新提交时更新反馈摘要
- **WHEN** 用户为某个轮次提交新反馈
- **THEN** 会话反馈摘要重新计算以包含新反馈

### Requirement: Feedback display in dashboard — 需求：仪表板中的反馈显示
系统应在对话分析仪表板中显示反馈指标：反馈量（总提交数）、反馈比率（positive / total）和随时间变化的反馈趋势。

#### Scenario: Dashboard shows feedback metrics — 场景：仪表板显示反馈指标
- **WHEN** 用户查看对话分析仪表板
- **THEN** 仪表板显示反馈量、反馈比率和所选时间范围内的反馈趋势图

#### Scenario: Filter by feedback status — 场景：按反馈状态过滤
- **WHEN** 用户按反馈状态（positive/negative/none）过滤对话
- **THEN** 仪表板仅显示匹配过滤条件的对话
