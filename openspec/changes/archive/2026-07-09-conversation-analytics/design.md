## Context

Hecate's ConversationModel groups messages under an agent. MessageModel stores individual turns (role, content, tool_calls, metadata_). SessionModel tracks execution state (status, conversation_id). TraceModel (populated by Change 1) has conversation_id, session_id, agent_id for every execution span.

The existing `ToolAnalyticsService` and `AgentHealthService` demonstrate the SQL-aggregation pattern: constructor takes `AsyncSession`, methods run `func.count()` / `func.max()` queries with `~Model.deleted` filter, P95 computed in Python for cross-dialect compatibility.

The existing `EvaluationEngine` (`services/evaluation/engine.py`) runs evaluators against dataset items (single-turn Q&A). It is **not** suitable for multi-turn conversation evaluation — it expects `EvalInput` (query, contexts, answer) per item, not a sequence of user/assistant messages. A new `ConversationQualityScorer` is needed.

The existing `ScheduleManager` (`services/scheduling/manager.py`) wraps APScheduler for periodic jobs. The existing embedding service (`services/rag/embedding.py`) generates embeddings via configurable providers. Qdrant is deployed for vector storage.

**Industry research (Salesforce, Bedrock, Langfuse, AgentLoop, Google Gemini):**
- Turn-level quality scoring is the standard (Salesforce "per interaction", Bedrock trace-level, Langfuse observation-level)
- LLM-as-Judge with 3 dimensions (helpfulness, coherence, instruction_adherence) achieves 80-90% agreement with human evaluators
- Event-driven scoring with sampling (Bedrock) or frequent batch (Google 10min) — not hourly batch
- LLM-guided topic clustering with embedding + LLM semantic confirmation (CATCH 2026, NILC 2025)
- User feedback at turn level (Salesforce, Dify) — not conversation level

## Goals / Non-Goals

**Goals:**

- Turn-level quality scoring using LLM-as-Judge (helpfulness, coherence, instruction_adherence)
- Turn-level user feedback capture (positive/negative rating + optional comment)
- LLM-guided topic clustering with embedding similarity + LLM semantic confirmation
- Event-driven scoring triggered on conversation completion with configurable sampling rate
- Conversation analytics dashboard: quality trends, topic distribution, low-quality drill-down
- REST API following `ToolAnalyticsService` pattern
- Frontend dashboard following `ops-center/tools/page.tsx` pattern

**Non-Goals:**

- Real-time per-turn scoring (scoring happens on conversation completion, not after each turn)
- Per-turn user feedback UI (feedback captured at conversation level for v1, aggregated to turns)
- Embedding model fine-tuning (use existing embedding service)
- Topic cluster visualization page (use existing dashboard patterns)
- Integration with external evaluation platforms (LangSmith, Langfuse) — future enhancement

## Decisions

### Decision 1: Turn-level quality scoring (not conversation-level)

**Choice**: Score each assistant turn independently using LLM-as-Judge. Each turn gets helpfulness, coherence, instruction_adherence scores. Conversation-level score is the average of turn scores.

**Rationale**: Industry research shows turn-level is the standard:
- Salesforce: "Quality Scores per interaction"
- Bedrock: trace-level evaluation (each user→assistant exchange)
- Langfuse: "observation-level evaluators for production — faster, more precise"
- AgentLoop: "assess the quality of each conversational turn"

A conversation may have 7 good turns and 1 bad turn. Conversation-level scoring would mark it as "medium", losing the "turn 5 has a problem" signal needed for root cause analysis.

**Alternatives considered**:
- **Conversation-level only**: Simpler but loses per-turn signal. Not aligned with industry standard.
- **Span-level (per LLM call)**: Too granular. Tool calls and LLM calls are implementation details, not user-facing quality signals.

### Decision 2: Event-driven scoring with sampling (Salesforce mode)

**Choice**: Quality scoring triggered asynchronously on conversation completion. Configurable sampling rate (default 1.0 = 100%). Scoring runs in `asyncio.create_task()`, does not block the chat response path.

**Rationale**: Industry research shows two patterns:
- Event-driven (Salesforce, Bedrock, Langfuse): real-time, scores appear within seconds
- Frequent batch (Google 10min, AgentLoop): low latency, simpler implementation

Event-driven is closer to Salesforce's approach and provides the lowest latency. Sampling controls cost — at 100% sampling, every conversation gets scored; at 10%, only 10% are scored.

The trigger point is conversation completion (not per-turn), which means:
- All turns in a conversation are scored together in one batch
- LLM call can include full conversation context for better evaluation
- Reduces LLM call frequency (1 call per conversation, not N calls per conversation)

**Alternatives considered**:
- **Per-turn event-driven**: Lower latency but N LLM calls per conversation (expensive). Can be added as enhancement.
- **APScheduler hourly batch**: Simpler but 1-hour latency. Not aligned with industry standard.
- **APScheduler 10-min batch**: Good latency but still batch-oriented. Event-driven is better.

### Decision 3: LLM-as-Judge with 3 dimensions

**Choice**: LLM judge evaluates each turn on 3 dimensions:
- `helpfulness` (0.0-1.0): Does the response address the user's need?
- `coherence` (0.0-1.0): Is the response logically consistent and well-structured?
- `instruction_adherence` (0.0-1.0): Does the response follow system prompt constraints?

Plus `topic` (categorical): Classify the conversation topic into predefined categories.

The judge prompt includes full conversation context (all prior turns) for accurate evaluation.

**Rationale**: These 3 dimensions are the most common across platforms:
- Bedrock: Helpfulness, Coherence, Instruction Following
- AgentLoop: toxicity, security, coherence, completeness
- Langfuse: Hallucination, Context-Relevance, Toxicity, Helpfulness

We omit toxicity/safety for v1 (can be added later). We add instruction_adherence because it's critical for detecting prompt drift.

Topic classification is merged into the same LLM call (zero extra cost), following Salesforce's "Intent Tags" approach.

**Alternatives considered**:
- **More dimensions (faithfulness, relevance, etc.)**: Increases prompt complexity and LLM cost. 3 dimensions is sufficient for v1.
- **Binary pass/fail**: Too coarse. Numeric scores enable trend analysis and threshold alerting.
- **Separate LLM call for topic**: Doubles cost. Merging into quality scoring call is more efficient.

### Decision 4: LLM-guided topic clustering (Option C)

**Choice**: Hybrid clustering approach:
1. **Embedding generation**: Conversation messages → embedding via existing RAG embedding service → store in Qdrant
2. **Initial clustering**: HDBSCAN on embeddings to discover topic clusters
3. **Incremental matching**: New conversation embedding → cosine similarity to existing clusters → LLM semantic confirmation for ambiguous matches
4. **Cluster labeling**: LLM generates topic labels from cluster contents
5. **Quality monitoring**: DBI, Silhouette, Cohesion scores to detect cluster degradation
6. **Refinement**: LLM-guided splitting of degraded clusters, merging of similar clusters

**Rationale**: Industry research shows LLM-guided clustering is the state-of-the-art:
- CATCH (2026 AAAI): embedding similarity + LLM semantic confirmation + preference-guided clustering
- NILC (2025): dual centroid scheme (Euclidean + semantic) with LLM-assisted refinement
- Alibaba LoongSuite: concern segmentation from multi-turn chats

Hecate already has embedding service + Qdrant + LLM service. The only new dependency is `hdbscan` (~2MB).

**Alternatives considered**:
- **Predefined categories only (Option A)**: Simpler but cannot discover new topics. Not aligned with industry trend.
- **Embedding + K-Means (Option B)**: Requires specifying k upfront. HDBSCAN auto-selects cluster count.
- **Full ML pipeline (BERTopic, etc.)**: Over-engineering for v1. HDBSCAN + LLM is sufficient.

### Decision 5: ConversationTurnScoreModel (not MessageModel.metadata_)

**Choice**: Store turn-level quality scores in a new `ConversationTurnScoreModel` table, not in `MessageModel.metadata_` JSON column.

**Rationale**:
- **Query performance**: Quality score distribution, low-quality turn ranking, topic aggregation all require efficient SQL queries. JSON queries are slow and non-indexable.
- **Data integrity**: Typed columns (float, str, datetime) enforce data quality. JSON columns accept anything.
- **Industry standard**: Salesforce uses Data 360 (separate data model), Langfuse uses Score model (separate from observation), AgentLoop uses separate evaluation records.
- **Extensibility**: Adding new metrics (faithfulness, toxicity) is a column addition, not a JSON schema change.

**Alternatives considered**:
- **MessageModel.metadata_**: Zero migration but poor query performance and no type safety.
- **ConversationModel.feedback**: Only supports conversation-level feedback, not turn-level.

### Decision 6: ConversationModel aggregate columns

**Choice**: Add aggregate columns to ConversationModel for fast dashboard queries:
- `quality_score: float | None` — average of all turn scores
- `quality_min_score: float | None` — lowest turn score (root cause indicator)
- `quality_scored_at: datetime | None` — last scoring timestamp
- `quality_metrics: dict | None` — aggregated dimension scores
- `topic: str | None` — LLM-classified topic (from last turn)
- `feedback_summary: dict | None` — aggregated feedback counts
- `cluster_id: UUID | None` — FK to ConversationClusterModel

**Rationale**: Dashboard queries need fast access to conversation-level aggregates. Querying ConversationTurnScoreModel for every conversation in the overview would be expensive. Pre-computed aggregates on ConversationModel enable single-table queries.

**Trade-off**: Aggregates may be slightly stale if a new turn is scored after the aggregate was computed. Acceptable for analytics (not real-time alerting).

## Risks / Trade-offs

- **[Risk] LLM-as-Judge cost** → At 100% sampling, every conversation triggers an LLM call. Mitigation: configurable sampling rate (default 1.0, can降到 0.1 for cost savings). Use cost-effective judge model (gpt-4o-mini).
- **[Risk] LLM-as-Judge latency** → Scoring a conversation with 10 turns takes 10+ seconds. Mitigation: async task doesn't block chat. Score appears in dashboard within seconds of conversation completion.
- **[Risk] Topic clustering quality** → HDBSCAN quality depends on embedding quality. Mitigation: use existing RAG embedding service (proven). Monitor DBI/Silhouette scores. LLM confirmation for ambiguous matches.
- **[Risk] New cluster creation spam** → If too many unmatched conversations, system creates too many tiny clusters. Mitigation: minimum cluster size threshold (default 10). Unmatched conversations accumulate in "unclassified pool" until threshold is met.
- **[Risk] ConversationModel migration** → Adding columns to ConversationModel requires alembic migration. Mitigation: single migration for all new tables and columns. No data backfill needed (new columns are nullable).
- **[Trade-off] Event-driven vs batch** → Event-driven provides lowest latency but adds complexity to ConversationService. Batch is simpler but higher latency. We chose event-driven for Salesforce alignment.
- **[Trade-off] Turn-level vs conversation-level scoring** → Turn-level is more granular but requires more LLM calls. Conversation-level is simpler but loses per-turn signal. We chose turn-level for industry alignment.

## Migration Plan

1. **Alembic migration**: Single migration adding ConversationTurnScoreModel, ConversationClusterModel, and new columns to ConversationModel. No data backfill needed (new columns are nullable).
2. **Feature flag**: `CONVERSATION_QUALITY_SCORING_ENABLED` (default False). Enable after migration is applied and verified.
3. **Rollback**: Disable feature flag. Migration is additive (new tables + nullable columns), so rollback is safe.
4. **Dependencies**: Add `hdbscan` to pyproject.toml `[llm]` dependency group.

## Open Questions

- **Judge model selection**: Which LLM to use for quality scoring? Options: gpt-4o-mini (cheap, fast), gpt-4o (accurate, expensive), claude-3-haiku (cheap, fast). Recommendation: gpt-4o-mini for v1, configurable via settings.
- **Embedding model for clustering**: Use the same embedding model as RAG pipeline, or a separate model? Recommendation: same model for consistency.
- **Topic categories**: What predefined topics to use? Options: generic (billing, technical, support, general) vs domain-specific. Recommendation: start with generic, configurable via settings.
- **Feedback UI placement**: Where to show 👍👎 buttons? Options: per-turn (inline with each message) vs per-conversation (at conversation end). Recommendation: per-conversation for v1 (simpler UI), upgrade to per-turn in v2.
