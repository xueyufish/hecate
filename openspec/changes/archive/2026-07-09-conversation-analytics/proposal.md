## Why — 动机

Hecate 无法了解对话质量。当一个 agent 在 8 轮对话的第 3 轮给出糟糕的回答时，没有系统可以检测到它 — 操作员只能看到会话级指标（延迟、错误率），这些指标会漏掉静默的质量下降。竞争平台（Salesforce Agentforce、Amazon Bedrock AgentCore、Alibaba AgentLoop）提供使用 LLM-as-Judge 的轮级质量评分、用于对话分段的主题聚类以及用户反馈捕获。Hecate 需要同样的能力。

此变更（功能 8.9b）是四个 Ops Center 变更中的第三个。它建立在追踪基础设施（变更 1）和 agent 健康监控（变更 2）的基础上，增加了对话级分析。路线图将此指定为 L 大小的变更，v1（统计 + 反馈）和 v2（异步 LLM 质量评分）一起交付 — 此变更涵盖两者。

## What Changes — 变更内容

- **新增：`ConversationTurnScoreModel`** — ORM 模型，存储每轮质量评分（helpfulness、coherence、instruction_adherence、overall_score）和每轮用户反馈（user_rating、user_comment）。每行代表一次 assistant 轮次的评估。通过 conversation_id 链接到 ConversationModel，通过 message_id 链接到 MessageModel。
- **新增：`ConversationClusterModel`** — ORM 模型，用于 LLM 引导聚类发现的主题簇。存储簇标签、质心嵌入、描述、质量指标（DBI、Silhouette、Cohesion 评分）和对话计数。
- **新增：`ConversationQualityScorer`** — 使用 LLM-as-Judge 评估对话轮次的服务。构建包含完整对话上下文的评估提示，对多个维度（helpfulness、coherence、instruction_adherence）进行评分，对主题进行分类，并返回结构化评分及推理。在对话完成时异步触发，具有可配置的采样率。
- **新增：`ConversationTopicMatcher`** — 将对话匹配到主题簇的服务。使用嵌入相似性（Qdrant 余弦）进行初始过滤，然后对模糊匹配进行 LLM 语义确认。在不匹配的对话积累时创建新簇。增量匹配 — 无需完全重新聚类。
- **新增：`ConversationClusterManager`** — 管理簇生命周期的服务：初始 HDBSCAN 聚类、簇标记（LLM 生成的主题名称）、质量监控（DBI、Silhouette、Cohesion 评分）和优化（拆分降级的簇、合并相似的簇）。
- **新增：`ConversationAnalyticsService`** — 查询 ConversationModel 和 ConversationTurnScoreModel 的聚合服务：会话量趋势、质量评分分布、主题分布、低质量对话向下钻取、反馈摘要。
- **新增：REST API** — `GET /api/ops-center/conversations/*` 端点，用于概览、质量分布、主题、低质量对话以及每个对话的向下钻取及轮级评分。
- **新增：前端仪表板** — 位于 `web/src/app/(dashboard)/ops-center/conversations/` 的对话分析仪表板，包含质量趋势、主题分布图、低质量对话列表和轮级评分详细信息视图。
- **新增：侧边栏子入口** — 在现有 "Ops Center" 部分下的 "Conversations" 导航项。
- **修改：`ConversationModel`** — 添加列：`quality_score`（float，聚合平均值）、`quality_min_score`（float，用于根本原因分析的最低轮次评分）、`quality_scored_at`（datetime）、`quality_metrics`（JSON，聚合维度评分）、`topic`（str，LLM 分类的主题）、`feedback_summary`（JSON，聚合反馈计数）、`cluster_id`（UUID FK 到 ConversationClusterModel）。
- **修改：`MessageModel`** — 无模式更改。轮次级评分存储在 ConversationTurnScoreModel 中（而非消息元数据），以提高查询性能和数据完整性。
- **新增：Alembic 迁移** — 单个迁移，添加 ConversationTurnScoreModel、ConversationClusterModel 和 ConversationModel 的新列。

## Capabilities — 能力

### New Capabilities — 新增能力

- `conversation-quality-scoring`：使用 LLM-as-Judge 的轮级对话质量评分。在对话完成时异步触发，具有可配置采样率。对每轮进行 helpfulness、coherence、instruction_adherence 评分。聚合为对话级指标。包括用于查询评分和向下钻取到单个轮次的 REST API。
- `conversation-feedback`：轮级用户反馈捕获。用户可以评分单个 assistant 轮次（positive/negative），附带可选评论。反馈与自动评分一起存储在 ConversationTurnScoreModel 中。反馈聚合为对话级摘要。
- `conversation-topic-clustering`：LLM 引导的对话主题聚类。结合嵌入相似性（Qdrant）与 LLM 语义确认，实现增量簇分配。在不匹配的对话积累时自动发现新主题。簇质量监控使用 DBI、Silhouette、Cohesion 评分。LLM 生成的主题标签。
- `conversation-analytics-dashboard`：显示对话分析的前端仪表板：会话量趋势、质量评分分布、主题分布、带向下钻取的低质量对话列表、轮级评分可视化、反馈摘要。

### Modified Capabilities — 修改的能力

（无 — 此变更引入新能力，不修改现有规范要求）

## Impact — 影响

- **Models 层**：新增 `ConversationTurnScoreModel` 和 `ConversationClusterModel` 表。`ConversationModel` 新增列（quality_score、quality_min_score、quality_scored_at、quality_metrics、topic、feedback_summary、cluster_id）。单个 alembic 迁移。
- **Services 层**：`services/ops_center/` 中新增 `ConversationQualityScorer`、`ConversationTopicMatcher`、`ConversationClusterManager`、`ConversationAnalyticsService`。
- **API 层**：`api/management/conversation_analytics.py` 中新增路由器。在 `main.py` 中注册。
- **配置**：新增设置（`CONVERSATION_QUALITY_SCORING_ENABLED`、`CONVERSATION_QUALITY_SAMPLING_RATE`、`CONVERSATION_QUALITY_JUDGE_MODEL`、`CONVERSATION_CLUSTERING_ENABLED`、`CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE`）。
- **前端**：新增 `ops-center/conversations/` 页面 + 侧边栏子入口。
- **依赖**：用于聚类的 `hdbscan` 包（新依赖，约 2MB）。重用现有的 `qdrant-client`、`sentence-transformers`（来自 RAG 管道）、`sqlalchemy`、`apscheduler`。
- **测试**：为质量评分器、主题匹配器、簇管理器、分析服务和 API 端点新增测试文件。
