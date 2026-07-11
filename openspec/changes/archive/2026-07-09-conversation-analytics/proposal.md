## Why

Hecate has no visibility into conversation quality. When an agent gives a poor answer at turn 3 of an 8-turn conversation, there is no system to detect it — operators see only session-level metrics (latency, error rate) that miss silent quality degradation. Competing platforms (Salesforce Agentforce, Amazon Bedrock AgentCore, Alibaba AgentLoop) provide turn-level quality scoring with LLM-as-Judge, topic clustering for conversation segmentation, and user feedback capture. Hecate needs the same.

This change (feature 8.9b) is the third of four Ops Center changes. It builds on the trace infrastructure (Change 1) and agent health monitoring (Change 2) to add conversation-level analytics. The roadmap specifies this as an L-sized change with v1 (statistics + feedback) and v2 (async LLM quality scoring) shipping together — this change covers both.

## What Changes

- **New: `ConversationTurnScoreModel`** — ORM model storing per-turn quality scores (helpfulness, coherence, instruction_adherence, overall_score) and per-turn user feedback (user_rating, user_comment). Each row represents one assistant turn's evaluation. Links to ConversationModel via conversation_id and MessageModel via message_id.
- **New: `ConversationClusterModel`** — ORM model for topic clusters discovered by LLM-guided clustering. Stores cluster label, centroid embedding, description, quality metrics (DBI, Silhouette, Cohesion scores), and conversation count.
- **New: `ConversationQualityScorer`** — Service that evaluates conversation turns using LLM-as-Judge. Constructs evaluation prompts with full conversation context, scores multiple dimensions (helpfulness, coherence, instruction_adherence), classifies topic, and returns structured scores with reasoning. Triggered asynchronously on conversation completion with configurable sampling rate.
- **New: `ConversationTopicMatcher`** — Service that matches conversations to topic clusters. Uses embedding similarity (Qdrant cosine) for initial filtering, then LLM semantic confirmation for ambiguous matches. Creates new clusters when unmatched conversations accumulate. Incremental matching — no full re-clustering.
- **New: `ConversationClusterManager`** — Service managing cluster lifecycle: initial HDBSCAN clustering, cluster labeling (LLM-generated topic names), quality monitoring (DBI, Silhouette, Cohesion scores), and refinement (split degraded clusters, merge similar clusters).
- **New: `ConversationAnalyticsService`** — Aggregation service querying ConversationModel and ConversationTurnScoreModel for analytics: session volume trends, quality score distribution, topic distribution, low-quality conversation drill-down, feedback summary.
- **New: REST API** — `GET /api/ops-center/conversations/*` endpoints for overview, quality distribution, topics, low-quality conversations, and per-conversation drill-down with turn-level scores.
- **New: Frontend dashboard** — Conversation analytics dashboard at `web/src/app/(dashboard)/ops-center/conversations/` with quality trends, topic distribution chart, low-quality conversation list, and turn-level score detail view.
- **New: Sidebar sub-entry** — "Conversations" navigation item under the existing "Ops Center" section.
- **Modified: `ConversationModel`** — Add columns: `quality_score` (float, aggregated average), `quality_min_score` (float, lowest turn score for root cause), `quality_scored_at` (datetime), `quality_metrics` (JSON, aggregated dimension scores), `topic` (str, LLM-classified topic), `feedback_summary` (JSON, aggregated feedback counts), `cluster_id` (UUID FK to ConversationClusterModel).
- **Modified: `MessageModel`** — No schema changes. Turn-level scores stored in ConversationTurnScoreModel (not in message metadata) for query performance and data integrity.
- **New: Alembic migration** — Single migration adding ConversationTurnScoreModel, ConversationClusterModel, and new columns to ConversationModel.

## Capabilities

### New Capabilities

- `conversation-quality-scoring`: Turn-level LLM-as-Judge quality scoring for conversations. Triggered asynchronously on conversation completion with configurable sampling. Scores helpfulness, coherence, instruction_adherence per turn. Aggregates to conversation-level metrics. Includes REST API for querying scores and drill-down to individual turns.
- `conversation-feedback`: Turn-level user feedback capture. Users can rate individual assistant turns (positive/negative) with optional comments. Feedback stored alongside automated scores in ConversationTurnScoreModel. Feedback aggregated to conversation-level summary.
- `conversation-topic-clustering`: LLM-guided topic clustering for conversations. Combines embedding similarity (Qdrant) with LLM semantic confirmation for incremental cluster assignment. Auto-discovers new topics when unmatched conversations accumulate. Cluster quality monitoring with DBI, Silhouette, Cohesion scores. LLM-generated topic labels.
- `conversation-analytics-dashboard`: Frontend dashboard displaying conversation analytics: session volume trends, quality score distribution, topic distribution, low-quality conversation list with drill-down, turn-level score visualization, feedback summary.

### Modified Capabilities

_(none — this change introduces new capabilities without modifying existing spec requirements)_

## Impact

- **Models layer**: New `ConversationTurnScoreModel` and `ConversationClusterModel` tables. New columns on `ConversationModel` (quality_score, quality_min_score, quality_scored_at, quality_metrics, topic, feedback_summary, cluster_id). Single alembic migration.
- **Services layer**: New `ConversationQualityScorer`, `ConversationTopicMatcher`, `ConversationClusterManager`, `ConversationAnalyticsService` in `services/ops_center/`.
- **API layer**: New router at `api/management/conversation_analytics.py`. Registered in `main.py`.
- **Config**: New settings (`CONVERSATION_QUALITY_SCORING_ENABLED`, `CONVERSATION_QUALITY_SAMPLING_RATE`, `CONVERSATION_QUALITY_JUDGE_MODEL`, `CONVERSATION_CLUSTERING_ENABLED`, `CONVERSATION_CLUSTERING_MIN_CLUSTER_SIZE`).
- **Frontend**: New `ops-center/conversations/` page + sidebar sub-entry.
- **Dependencies**: `hdbscan` package for clustering (new dependency, ~2MB). Reuses existing `qdrant-client`, `sentence-transformers` (from RAG pipeline), `sqlalchemy`, `apscheduler`.
- **Tests**: New test files for quality scorer, topic matcher, cluster manager, analytics service, and API endpoints.
