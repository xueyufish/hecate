## 1. Configuration

- [x] 1.1 Add quality scoring settings to `core/config.py`: `CONVERSATION_QUALITY_SCORING_ENABLED: bool = True`, `CONVERSATION_QUALITY_SAMPLING_RATE: float = 1.0`, `CONVERSATION_QUALITY_JUDGE_MODEL: str = "gpt-4o-mini"`
- [x] 1.2 Add clustering settings to `core/config.py`: `CONVERSATION_CLUSTERING_ENABLED: bool = True`, `CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE: int = 10`, `CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD: float = 0.5`, `CONVERSATION_CLUSTERING_CONFIRMATION_THRESHOLD: float = 0.8`

## 2. Data Models

- [x] 2.1 Create `src/hecate/models/conversation_turn_score.py` with `ConversationTurnScoreModel` (conversation_id, message_id, turn_index, helpfulness, coherence, instruction_adherence, overall_score, reasoning, user_rating, user_comment, user_id, feedback_at, scored_at)
- [x] 2.2 Create `src/hecate/models/conversation_cluster.py` with `ConversationClusterModel` (label, centroid_embedding, description, conversation_count, dbi_score, silhouette_score, cohesion_score, created_at, updated_at)
- [x] 2.3 Add new columns to `ConversationModel` in `src/hecate/models/conversation.py`: quality_score, quality_min_score, quality_scored_at, quality_metrics, topic, feedback_summary, cluster_id
- [x] 2.4 Create alembic migration for new tables and ConversationModel columns

## 3. ConversationQualityScorer — Core Logic

- [x] 3.1 Create `src/hecate/services/ops_center/conversation_quality_scorer.py` with `ConversationQualityScorer` class (constructor takes `AsyncSession`)
- [x] 3.2 Implement `_build_judge_prompt(messages, turn_index)` → LLM prompt with full conversation context and scoring rubric
- [x] 3.3 Implement `_parse_judge_response(response)` → extract helpfulness, coherence, instruction_adherence, topic, reasoning from LLM JSON response
- [x] 3.4 Implement `score_turn(conversation_id, messages, turn_index)` → call LLM, parse response, create ConversationTurnScoreModel record
- [x] 3.5 Implement `score_conversation(conversation_id)` → load all messages, score each assistant turn, aggregate to conversation-level
- [x] 3.6 Implement `_aggregate_to_conversation(conversation_id, turn_scores)` → compute quality_score, quality_min_score, quality_metrics, update ConversationModel

## 4. ConversationQualityScorer — Tests

- [x] 4.1 Test `score_turn()` with single turn (verify helpfulness, coherence, instruction_adherence scores saved)
- [x] 4.2 Test `score_conversation()` with 3-turn conversation (verify 3 turn records created)
- [x] 4.3 Test `_aggregate_to_conversation()` computes correct quality_score (average) and quality_min_score (minimum)
- [x] 4.4 Test error handling: LLM call fails for one turn → skip turn, continue scoring remaining turns
- [x] 4.5 Test sampling: sampling_rate=0.0 → no scoring triggered
- [x] 4.6 Test judge model configuration: custom model name used in LLM call

## 5. ConversationEmbeddingService

- [x] 5.1 Create `src/hecate/services/ops_center/conversation_embedding.py` with `ConversationEmbeddingService` class
- [x] 5.2 Implement `generate_embedding(conversation_id)` → load messages, concatenate content, call RAG embedding service, store in Qdrant conversation_embeddings collection
- [x] 5.3 Implement `get_embedding(conversation_id)` → retrieve embedding from Qdrant

## 6. ConversationTopicMatcher — Incremental Matching

- [x] 6.1 Create `src/hecate/services/ops_center/conversation_topic_matcher.py` with `ConversationTopicMatcher` class
- [x] 6.2 Implement `match_to_cluster(conversation_id)` → get embedding, cosine similarity to existing clusters, return best match or None
- [x] 6.3 Implement `_cosine_match(embedding, threshold)` → query Qdrant for top-5 similar clusters, return matches above threshold
- [x] 6.4 Implement `_llm_confirm_match(conversation_messages, candidate_clusters)` → LLM selects best cluster from candidates
- [x] 6.5 Implement `_create_new_cluster(conversation_ids)` → HDBSCAN on unclassified pool, create ConversationClusterModel if cluster found

## 7. ConversationClusterManager — Quality Monitoring

- [x] 7.1 Create `src/hecate/services/ops_center/conversation_cluster_manager.py` with `ConversationClusterManager` class
- [x] 7.2 Implement `compute_cluster_quality(cluster_id)` → compute DBI, Silhouette, Cohesion scores for a cluster
- [x] 7.3 Implement `generate_cluster_label(cluster_id)` → sample conversations from cluster, LLM generates topic label
- [x] 7.4 Implement `refine_clusters()` → detect degraded clusters (Silhouette < 0.5), split overly broad clusters, merge similar clusters (centroid similarity > 0.9)
- [x] 7.5 Implement `run_initial_clustering()` → HDBSCAN on all unclustered embeddings, create clusters with labels

## 8. ConversationTopicMatcher + ClusterManager — Tests

- [x] 8.1 Test `match_to_cluster()` with high similarity match (> 0.8) → direct assignment
- [x] 8.2 Test `match_to_cluster()` with ambiguous match (0.5–0.8) → LLM confirmation called
- [x] 8.3 Test `match_to_cluster()` with no match (< 0.5) → returns None
- [x] 8.4 Test `_create_new_cluster()` accumulates unclassified conversations and creates cluster when threshold met
- [x] 8.5 Test `compute_cluster_quality()` returns correct DBI, Silhouette, Cohesion scores
- [x] 8.6 Test `generate_cluster_label()` calls LLM and stores label
- [x] 8.7 Test `refine_clusters()` splits degraded cluster and merges similar clusters

## 9. Event-Driven Scoring Trigger

- [x] 9.1 Add `_maybe_score_conversation()` method to `ConversationService` in `src/hecate/services/conversation.py`
- [x] 9.2 Implement sampling logic: check `CONVERSATION_QUALITY_SAMPLING_RATE`, skip if random() > rate
- [x] 9.3 Implement async trigger: `asyncio.create_task()` to run quality scorer, then embedding service, then topic matcher
- [x] 9.4 Hook `_maybe_score_conversation()` into conversation completion flow (when status changes to "completed")
- [x] 9.5 Add error handling: log failures, don't block chat response

## 10. ConversationAnalyticsService — Aggregation

- [x] 10.1 Create `src/hecate/services/ops_center/conversation_analytics.py` with `ConversationAnalyticsService` class
- [x] 10.2 Implement `get_overview(start_date, end_date)` → total conversations, scored conversations, avg quality score, quality distribution, feedback summary
- [x] 10.3 Implement `get_quality_distribution(start_date, end_date)` → histogram of quality scores (buckets: 0.0–0.2, 0.2–0.4, 0.4–0.6, 0.6–0.8, 0.8–1.0)
- [x] 10.4 Implement `get_topics(start_date, end_date)` → topic distribution with conversation count and avg quality score
- [x] 10.5 Implement `get_low_quality(threshold, start_date, end_date)` → conversations with quality_score < threshold, sorted ascending
- [x] 10.6 Implement `get_conversation_turns(conversation_id)` → all turn scores for a conversation, ordered by turn_index
- [x] 10.7 Implement `get_trends(granularity, days)` → time-series of conversation count, avg quality score, feedback ratio

## 11. ConversationAnalyticsService — Tests

- [x] 11.1 Test `get_overview()` with mixed scored/unscored conversations
- [x] 11.2 Test `get_quality_distribution()` returns correct histogram buckets
- [x] 11.3 Test `get_topics()` returns topic distribution with counts and avg quality
- [x] 11.4 Test `get_low_quality()` returns conversations below threshold sorted ascending
- [x] 11.5 Test `get_conversation_turns()` returns turn scores ordered by turn_index
- [x] 11.6 Test `get_trends()` returns correct daily/weekly data points

## 12. Feedback API

- [x] 12.1 Implement `POST /api/ops-center/conversations/{id}/turns/{turn_index}/feedback` endpoint in conversation analytics router
- [x] 12.2 Implement feedback validation: rating must be "positive" or "negative", comment is optional
- [x] 12.3 Implement feedback storage: update ConversationTurnScoreModel with user_rating, user_comment, feedback_at
- [x] 12.4 Implement feedback summary update: recompute conversation feedback_summary after feedback submission

## 13. Conversation Analytics API Router

- [x] 13.1 Create `src/hecate/api/management/conversation_analytics.py` router with prefix `/api/ops-center/conversations`
- [x] 13.2 Implement `GET /overview` endpoint (start_date, end_date query params)
- [x] 13.3 Implement `GET /quality-distribution` endpoint (start_date, end_date query params)
- [x] 13.4 Implement `GET /topics` endpoint (start_date, end_date query params)
- [x] 13.5 Implement `GET /low-quality` endpoint (threshold, start_date, end_date query params)
- [x] 13.6 Implement `GET /{id}/turns` endpoint (per-turn scores for a conversation)
- [x] 13.7 Implement `GET /trends` endpoint (granularity, days query params)
- [x] 13.8 Register `conversation_analytics_router` in `main.py`

## 14. Frontend — Conversation Analytics Dashboard

- [x] 14.1 Create `web/src/app/(dashboard)/ops-center/conversations/page.tsx` with overview cards (total conversations, scored conversations, avg quality score, feedback ratio)
- [x] 14.2 Add quality distribution bar chart (color-coded: red <0.4, yellow 0.4–0.7, green >0.7)
- [x] 14.3 Add topic distribution chart (pie or bar chart)
- [x] 14.4 Add low-quality conversation list table (conversation ID, agent name, quality score, topic, turn count, last active)
- [x] 14.5 Add time range selector (24h / 7d / 30d) that re-fetches all data
- [x] 14.6 Add drill-down: clicking a conversation navigates to turn-level detail view
- [x] 14.7 Add turn-level detail view (turn cards with message previews, quality scores, reasoning, user feedback)
- [x] 14.8 Add feedback metrics display (volume, ratio, trend chart)
- [x] 14.9 Add empty state handling (no conversations / no scored conversations)
- [x] 14.10 Add "Conversations" sub-navigation item under "Ops Center" in `web/src/components/sidebar.tsx`

## 15. Verification

- [x] 15.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 15.2 Run `mypy src/` — 0 errors
- [x] 15.3 Run `python -m pytest tests/test_ops_center/test_conversation_quality_scorer.py tests/test_ops_center/test_conversation_topic_matcher.py tests/test_ops_center/test_conversation_cluster_manager.py tests/test_ops_center/test_conversation_analytics.py -q` — all pass
- [x] 15.4 Verify end-to-end: complete a conversation, confirm quality scores appear in dashboard
