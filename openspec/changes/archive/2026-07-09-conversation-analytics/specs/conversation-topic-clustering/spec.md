## ADDED Requirements — 新增需求

### Requirement: Conversation embedding generation — 需求：对话嵌入生成
系统应使用现有的 RAG 嵌入服务为完成的对话生成嵌入。嵌入应从对话的消息内容（拼接的用户和 assistant 消息）计算。嵌入应存储在 Qdrant 的专用 `conversation_embeddings` 集合中。

#### Scenario: Generate embedding for a completed conversation — 场景：为完成的对话生成嵌入
- **WHEN** 对话完成并触发质量评分
- **THEN** 系统从对话的消息内容生成嵌入，并将对话 ID 作为点 ID 存储在 Qdrant 中

#### Scenario: Embedding generation uses existing RAG service — 场景：嵌入生成使用现有的 RAG 服务
- **WHEN** 系统需要生成对话嵌入
- **THEN** 它使用为 RAG 管道配置的相同嵌入模型和提供商

### Requirement: Initial topic clustering via HDBSCAN — 需求：通过 HDBSCAN 的初始主题聚类
系统应使用 HDBSCAN 对对话嵌入执行初始主题聚类。系统应自动确定簇的数量（无预定义 k）。簇应存储在 ConversationClusterModel 中，包含标签、质心嵌入和质量指标。

#### Scenario: Initial clustering discovers topic clusters — 场景：初始聚类发现主题簇
- **WHEN** 100 个未聚类的对话积累
- **THEN** 系统对其嵌入运行 HDBSCAN，并为每个发现的簇创建 ConversationClusterModel 记录

#### Scenario: HDBSCAN auto-selects cluster count — 场景：HDBSCAN 自动选择簇数量
- **WHEN** HDBSCAN 在 100 个嵌入上运行
- **THEN** 它根据密度自动确定最佳簇数量（例如，5 个簇）

#### Scenario: Cluster centroid computed — 场景：计算簇质心
- **WHEN** 使用 20 个对话嵌入创建一个簇
- **THEN** 系统计算质心（平均嵌入）并将其存储在簇记录中

### Requirement: Incremental conversation-to-cluster matching — 需求：增量对话到簇匹配
系统应使用嵌入余弦相似度将新对话匹配到现有簇。系统应使用两阶段方法：（1）余弦相似度过滤，找到前 5 个候选簇；（2）对模糊匹配进行 LLM 语义确认。不匹配的对话应在"未分类池"中积累，直到足够多的相似对话形成新簇。

#### Scenario: New conversation matches existing cluster — 场景：新对话匹配现有簇
- **WHEN** 新对话嵌入与现有簇质心的余弦相似度 > 0.8
- **THEN** 系统将该对话分配到该簇，无需 LLM 确认

#### Scenario: Ambiguous match requires LLM confirmation — 场景：模糊匹配需要 LLM 确认
- **WHEN** 新对话嵌入与多个簇的余弦相似度在 0.5 到 0.8 之间
- **THEN** 系统使用 LLM 确认哪个簇是最佳匹配

#### Scenario: No match found — 场景：未找到匹配
- **WHEN** 新对话嵌入与所有簇的余弦相似度 < 0.5
- **THEN** 系统将该对话添加到"未分类池"

#### Scenario: New cluster creation from unclassified pool — 场景：从未分类池创建新簇
- **WHEN** 未分类池积累了 10+ 个相似对话
- **THEN** 系统使用 LLM 确定它们是否形成有意义的新簇，如果是则创建一个

### Requirement: LLM-generated topic labels — 需求：LLM 生成的主题标签
系统应使用 LLM 为每个簇生成人类可读的主题标签。LLM 应分析簇中的一组对话样本，并生成简洁的标签（例如，"billing"、"technical_support"、"feature_request"）。标签应存储在 ConversationClusterModel.label 中。

#### Scenario: Generate label for new cluster — 场景：为新簇生成标签
- **WHEN** 使用 15 个对话创建新簇
- **THEN** 系统采样 5 个对话，发送给 LLM，并收到类似 "technical_support" 的标签

#### Scenario: Label update when cluster content changes — 场景：簇内容变化时更新标签
- **WHEN** 簇的内容发生显著变化（例如，添加了 50% 的新对话）
- **THEN** 系统使用更新后的簇内容重新生成标签

### Requirement: Cluster quality monitoring — 需求：簇质量监控
系统应为每个簇计算质量指标：DBI（Davies-Bouldin Index）、Silhouette Score 和 Cohesion Score（簇内相似度）。系统应监控这些指标，并在指标低于阈值时标记簇。

#### Scenario: Compute cluster quality metrics — 场景：计算簇质量指标
- **WHEN** 一个簇有 20 个对话嵌入
- **THEN** 系统计算 DBI、Silhouette 和 Cohesion 评分并存储在簇记录中

#### Scenario: Detect cluster degradation — 场景：检测簇降级
- **WHEN** 簇的 Silhouette 评分降至 0.5 以下
- **THEN** 系统标记该簇以进行优化

### Requirement: Cluster refinement via splitting and merging — 需求：通过拆分和合并的簇优化
系统应通过拆分过于宽泛的簇和合并相似的簇来优化降级的簇。拆分应使用 LLM 识别簇内的子主题。合并应合并质心相似度 > 0.9 的簇。

#### Scenario: Split degraded cluster — 场景：拆分降级的簇
- **WHEN** 一个簇的 Silhouette 评分 < 0.5 且包含 50+ 个对话
- **THEN** 系统使用 LLM 识别 2-3 个子主题并相应地拆分该簇

#### Scenario: Merge similar clusters — 场景：合并相似簇
- **WHEN** 两个簇的质心相似度 > 0.9
- **THEN** 系统将它们合并为单个簇并重新生成标签

### Requirement: Topic clustering configuration — 需求：主题聚类配置
系统应支持可配置的设置：`CONVERSATION_CLUSTERING_ENABLED`（默认 True）、`CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE`（默认 10）、`CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD`（默认 0.5）、`CONVERSATION_CLUSTERING_CONFIRMATION_THRESHOLD`（默认 0.8）。

#### Scenario: Clustering disabled via configuration — 场景：通过配置禁用聚类
- **WHEN** `CONVERSATION_CLUSTERING_ENABLED` 为 False
- **THEN** 系统跳过嵌入生成和簇匹配

#### Scenario: Custom minimum cluster size — 场景：自定义最小簇大小
- **WHEN** `CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE` 设置为 20
- **THEN** 仅在积累 20+ 个相似未分类对话时才创建新簇

### Requirement: Topic distribution API — 需求：主题分布 API
系统应暴露 `GET /api/ops-center/conversations/topics`，返回主题分布：主题列表及其对话数量和平均质量评分。支持 `start_date`、`end_date` 查询参数。

#### Scenario: Get topic distribution — 场景：获取主题分布
- **WHEN** 客户端请求 `GET /api/ops-center/conversations/topics?start_date=...&end_date=...`
- **THEN** 系统返回 `[{topic: "technical_support", count: 45, avg_quality: 0.72}, {topic: "billing", count: 30, avg_quality: 0.85}]`

#### Scenario: Topic distribution includes unclassified — 场景：主题分布包括未分类
- **WHEN** 一些对话尚未聚类
- **THEN** 系统包含一个 "unclassified" 主题条目，计数为未聚类对话的数量
