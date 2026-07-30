## 1. Configuration — 配置

- [x] 1.1 在 `core/config.py` 中添加质量评分设置：`CONVERSATION_QUALITY_SCORING_ENABLED: bool = True`、`CONVERSATION_QUALITY_SAMPLING_RATE: float = 1.0`、`CONVERSATION_QUALITY_JUDGE_MODEL: str = "gpt-4o-mini"`
- [x] 1.2 在 `core/config.py` 中添加聚类设置：`CONVERSATION_CLUSTERING_ENABLED: bool = True`、`CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE: int = 10`、`CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD: float = 0.5`、`CONVERSATION_CLUSTERING_CONFIRMATION_THRESHOLD: float = 0.8`

## 2. Data Models — 数据模型

- [x] 2.1 创建 `src/hecate/models/conversation_turn_score.py`，包含 `ConversationTurnScoreModel`（conversation_id、message_id、turn_index、helpfulness、coherence、instruction_adherence、overall_score、reasoning、user_rating、user_comment、user_id、feedback_at、scored_at）
- [x] 2.2 创建 `src/hecate/models/conversation_cluster.py`，包含 `ConversationClusterModel`（label、centroid_embedding、description、conversation_count、dbi_score、silhouette_score、cohesion_score、created_at、updated_at）
- [x] 2.3 在 `src/hecate/models/conversation.py` 的 `ConversationModel` 中添加新列：quality_score、quality_min_score、quality_scored_at、quality_metrics、topic、feedback_summary、cluster_id
- [x] 2.4 为新表和 ConversationModel 列创建 alembic 迁移

## 3. ConversationQualityScorer — Core Logic — ConversationQualityScorer — 核心逻辑

- [x] 3.1 创建 `src/hecate/services/ops_center/conversation_quality_scorer.py`，包含 `ConversationQualityScorer` 类（构造函数接受 `AsyncSession`）
- [x] 3.2 实现 `_build_judge_prompt(messages, turn_index)` → 包含完整对话上下文和评分规范的 LLM 提示
- [x] 3.3 实现 `_parse_judge_response(response)` → 从 LLM JSON 响应中提取 helpfulness、coherence、instruction_adherence、topic、reasoning
- [x] 3.4 实现 `score_turn(conversation_id, messages, turn_index)` → 调用 LLM、解析响应、创建 ConversationTurnScoreModel 记录
- [x] 3.5 实现 `score_conversation(conversation_id)` → 加载所有消息、评分每个 assistant 轮次、聚合到对话级
- [x] 3.6 实现 `_aggregate_to_conversation(conversation_id, turn_scores)` → 计算 quality_score、quality_min_score、quality_metrics、更新 ConversationModel

## 4. ConversationQualityScorer — Tests — ConversationQualityScorer — 测试

- [x] 4.1 使用单轮测试 `score_turn()`（验证 helpfulness、coherence、instruction_adherence 评分已保存）
- [x] 4.2 使用 3 轮对话测试 `score_conversation()`（验证创建了 3 条轮次记录）
- [x] 4.3 测试 `_aggregate_to_conversation()` 计算正确的 quality_score（平均值）和 quality_min_score（最小值）
- [x] 4.4 错误处理测试：LLM 调用在一个轮次失败 → 跳过该轮次，继续评分剩余轮次
- [x] 4.5 采样测试：sampling_rate=0.0 → 不触发评分
- [x] 4.6 评估模型配置测试：LLM 调用中使用自定义模型名称

## 5. ConversationEmbeddingService — 对话嵌入服务

- [x] 5.1 创建 `src/hecate/services/ops_center/conversation_embedding.py`，包含 `ConversationEmbeddingService` 类
- [x] 5.2 实现 `generate_embedding(conversation_id)` → 加载消息、拼接内容、调用 RAG 嵌入服务、存储在 Qdrant conversation_embeddings 集合中
- [x] 5.3 实现 `get_embedding(conversation_id)` → 从 Qdrant 检索嵌入

## 6. ConversationTopicMatcher — Incremental Matching — ConversationTopicMatcher — 增量匹配

- [x] 6.1 创建 `src/hecate/services/ops_center/conversation_topic_matcher.py`，包含 `ConversationTopicMatcher` 类
- [x] 6.2 实现 `match_to_cluster(conversation_id)` → 获取嵌入、与现有簇进行余弦相似度计算、返回最佳匹配或 None
- [x] 6.3 实现 `_cosine_match(embedding, threshold)` → 查询 Qdrant 获取前 5 个相似簇、返回高于阈值的匹配
- [x] 6.4 实现 `_llm_confirm_match(conversation_messages, candidate_clusters)` → LLM 从候选项中选择最佳簇
- [x] 6.5 实现 `_create_new_cluster(conversation_ids)` → 对未分类池运行 HDBSCAN、如果发现簇则创建 ConversationClusterModel

## 7. ConversationClusterManager — Quality Monitoring — ConversationClusterManager — 质量监控

- [x] 7.1 创建 `src/hecate/services/ops_center/conversation_cluster_manager.py`，包含 `ConversationClusterManager` 类
- [x] 7.2 实现 `compute_cluster_quality(cluster_id)` → 计算簇的 DBI、Silhouette、Cohesion 评分
- [x] 7.3 实现 `generate_cluster_label(cluster_id)` → 从簇中采样对话、LLM 生成主题标签
- [x] 7.4 实现 `refine_clusters()` → 检测降级簇（Silhouette < 0.5）、拆分过宽的簇、合并相似簇（质心相似度 > 0.9）
- [x] 7.5 实现 `run_initial_clustering()` → 对所有未聚类嵌入运行 HDBSCAN、创建带标签的簇

## 8. ConversationTopicMatcher + ClusterManager — Tests — ConversationTopicMatcher + ClusterManager — 测试

- [x] 8.1 使用高相似度匹配（> 0.8）测试 `match_to_cluster()` → 直接分配
- [x] 8.2 使用模糊匹配（0.5–0.8）测试 `match_to_cluster()` → 调用 LLM 确认
- [x] 8.3 使用无匹配（< 0.5）测试 `match_to_cluster()` → 返回 None
- [x] 8.4 测试 `_create_new_cluster()` 积累未分类对话并在达到阈值时创建簇
- [x] 8.5 测试 `compute_cluster_quality()` 返回正确的 DBI、Silhouette、Cohesion 评分
- [x] 8.6 测试 `generate_cluster_label()` 调用 LLM 并存储标签
- [x] 8.7 测试 `refine_clusters()` 拆分降级簇并合并相似簇

## 9. Event-Driven Scoring Trigger — 事件驱动的评分触发

- [x] 9.1 在 `src/hecate/services/conversation.py` 的 `ConversationService` 中添加 `_maybe_score_conversation()` 方法
- [x] 9.2 实现采样逻辑：检查 `CONVERSATION_QUALITY_SAMPLING_RATE`、如果 random() > rate 则跳过
- [x] 9.3 实现异步触发：`asyncio.create_task()` 运行质量评分器、然后运行嵌入服务、然后运行主题匹配器
- [x] 9.4 将 `_maybe_score_conversation()` 挂接到对话完成流程（当状态更改为 "completed" 时）
- [x] 9.5 添加错误处理：记录失败、不阻塞聊天响应

## 10. ConversationAnalyticsService — Aggregation — ConversationAnalyticsService — 聚合

- [x] 10.1 创建 `src/hecate/services/ops_center/conversation_analytics.py`，包含 `ConversationAnalyticsService` 类
- [x] 10.2 实现 `get_overview(start_date, end_date)` → 总会话数、已评分会话数、平均质量评分、质量分布、反馈摘要
- [x] 10.3 实现 `get_quality_distribution(start_date, end_date)` → 质量评分直方图（桶：0.0–0.2、0.2–0.4、0.4–0.6、0.6–0.8、0.8–1.0）
- [x] 10.4 实现 `get_topics(start_date, end_date)` → 主题分布，含对话数量和平均质量评分
- [x] 10.5 实现 `get_low_quality(threshold, start_date, end_date)` → quality_score < 阈值的对话，升序排列
- [x] 10.6 实现 `get_conversation_turns(conversation_id)` → 对话的所有轮次评分，按 turn_index 排序
- [x] 10.7 实现 `get_trends(granularity, days)` → 对话数量、平均质量评分、反馈比率的时间序列

## 11. ConversationAnalyticsService — Tests — ConversationAnalyticsService — 测试

- [x] 11.1 使用混合已评分/未评分对话测试 `get_overview()`
- [x] 11.2 测试 `get_quality_distribution()` 返回正确的直方图桶
- [x] 11.3 测试 `get_topics()` 返回主题分布、计数和平均质量
- [x] 11.4 测试 `get_low_quality()` 返回低于阈值且升序排列的对话
- [x] 11.5 测试 `get_conversation_turns()` 返回按 turn_index 排序的轮次评分
- [x] 11.6 测试 `get_trends()` 返回正确的每日/每周数据点

## 12. Feedback API — 反馈 API

- [x] 12.1 在对话分析路由器中实现 `POST /api/ops-center/conversations/{id}/turns/{turn_index}/feedback` 端点
- [x] 12.2 实现反馈验证：rating 必须为 "positive" 或 "negative"，comment 为可选
- [x] 12.3 实现反馈存储：使用 user_rating、user_comment、feedback_at 更新 ConversationTurnScoreModel
- [x] 12.4 实现反馈摘要更新：反馈提交后重新计算对话 feedback_summary

## 13. Conversation Analytics API Router — 对话分析 API 路由器

- [x] 13.1 创建 `src/hecate/api/management/conversation_analytics.py` 路由器，前缀为 `/api/ops-center/conversations`
- [x] 13.2 实现 `GET /overview` 端点（start_date、end_date 查询参数）
- [x] 13.3 实现 `GET /quality-distribution` 端点（start_date、end_date 查询参数）
- [x] 13.4 实现 `GET /topics` 端点（start_date、end_date 查询参数）
- [x] 13.5 实现 `GET /low-quality` 端点（threshold、start_date、end_date 查询参数）
- [x] 13.6 实现 `GET /{id}/turns` 端点（每个对话的轮级评分）
- [x] 13.7 实现 `GET /trends` 端点（granularity、days 查询参数）
- [x] 13.8 在 `main.py` 中注册 `conversation_analytics_router`

## 14. Frontend — Conversation Analytics Dashboard — 前端 — 对话分析仪表板

- [x] 14.1 创建 `web/src/app/(dashboard)/ops-center/conversations/page.tsx`，包含概览卡片（总会话数、已评分会话数、平均质量评分、反馈比率）
- [x] 14.2 添加质量分布柱状图（颜色编码：红色 <0.4、黄色 0.4–0.7、绿色 >0.7）
- [x] 14.3 添加主题分布图（饼图或柱状图）
- [x] 14.4 添加低质量对话列表表（对话 ID、agent 名称、质量评分、主题、轮次数、最后活跃）
- [x] 14.5 添加时间范围选择器（24h / 7d / 30d），重新获取所有数据
- [x] 14.6 添加向下钻取：点击对话导航到轮级详细信息视图
- [x] 14.7 添加轮级详细信息视图（轮次卡片，包含消息预览、质量评分、推理、用户反馈）
- [x] 14.8 添加反馈指标显示（数量、比率、趋势图）
- [x] 14.9 添加空状态处理（无对话 / 无已评分对话）
- [x] 14.10 在 `web/src/components/sidebar.tsx` 中 "Ops Center" 下添加 "Conversations" 子导航项

## 15. Verification — 验证

- [x] 15.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 15.2 运行 `mypy src/` — 0 错误
- [x] 15.3 运行 `python -m pytest tests/test_ops_center/test_conversation_quality_scorer.py tests/test_ops_center/test_conversation_topic_matcher.py tests/test_ops_center/test_conversation_cluster_manager.py tests/test_ops_center/test_conversation_analytics.py -q` — 全部通过
- [x] 15.4 端到端验证：完成一个对话，确认质量评分出现在仪表板中
