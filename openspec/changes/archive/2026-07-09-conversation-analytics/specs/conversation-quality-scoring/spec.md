## ADDED Requirements

### Requirement: Turn-level quality scoring via LLM-as-Judge
The system SHALL evaluate each assistant turn in a completed conversation using LLM-as-Judge. The judge SHALL assess three dimensions: helpfulness (0.0–1.0), coherence (0.0–1.0), and instruction_adherence (0.0–1.0). Each dimension score SHALL include reasoning text explaining the assessment. The judge prompt SHALL include full conversation context (all prior turns) for accurate evaluation.

#### Scenario: Score a single-turn conversation
- **WHEN** a conversation with 1 user message and 1 assistant message completes
- **THEN** the system creates 1 ConversationTurnScoreModel record with helpfulness, coherence, instruction_adherence scores and reasoning for the assistant turn

#### Scenario: Score a multi-turn conversation
- **WHEN** a conversation with 3 user messages and 3 assistant messages completes
- **THEN** the system creates 3 ConversationTurnScoreModel records, one for each assistant turn, with scores reflecting each turn's quality in context

#### Scenario: LLM judge returns structured scores
- **WHEN** the LLM judge evaluates a turn
- **THEN** the response contains helpfulness (0.0–1.0), coherence (0.0–1.0), instruction_adherence (0.0–1.0), topic (categorical), and reasoning (string)

### Requirement: Event-driven scoring trigger on conversation completion
The system SHALL trigger quality scoring asynchronously when a conversation status changes to "completed". Scoring SHALL NOT block the chat response path. The system SHALL use `asyncio.create_task()` to run scoring in the background.

#### Scenario: Conversation completes and scoring is triggered
- **WHEN** a conversation status changes to "completed" and `CONVERSATION_QUALITY_SCORING_ENABLED` is True
- **THEN** the system creates an async task to score all turns in the conversation

#### Scenario: Scoring is disabled via configuration
- **WHEN** `CONVERSATION_QUALITY_SCORING_ENABLED` is False
- **THEN** no scoring task is created when a conversation completes

#### Scenario: Scoring does not block chat response
- **WHEN** a conversation completes and scoring is triggered
- **THEN** the chat response is returned to the user immediately without waiting for scoring to complete

### Requirement: Configurable sampling rate
The system SHALL support a configurable sampling rate (`CONVERSATION_QUALITY_SAMPLING_RATE`, default 1.0) that controls what percentage of completed conversations are scored. At rate 1.0, all conversations are scored. At rate 0.1, only 10% are scored.

#### Scenario: Sampling rate 1.0 scores all conversations
- **WHEN** sampling rate is 1.0 and 10 conversations complete
- **THEN** all 10 conversations are scored

#### Scenario: Sampling rate 0.5 scores half of conversations
- **WHEN** sampling rate is 0.5 and 10 conversations complete
- **THEN** approximately 5 conversations are scored (random selection)

#### Scenario: Sampling rate 0.0 disables scoring
- **WHEN** sampling rate is 0.0 and a conversation completes
- **THEN** no scoring task is created

### Requirement: Conversation-level score aggregation
The system SHALL compute conversation-level aggregate scores from turn-level scores: `quality_score` (average of all turn overall_scores), `quality_min_score` (lowest turn overall_score), `quality_metrics` (per-dimension averages), and `quality_scored_at` (timestamp). These aggregates SHALL be stored on the ConversationModel.

#### Scenario: Aggregate scores computed from 3 turns
- **WHEN** a conversation has 3 turns with overall_scores [0.9, 0.4, 0.8]
- **THEN** conversation quality_score is 0.7 (average), quality_min_score is 0.4 (minimum)

#### Scenario: Aggregate updated when new turn is scored
- **WHEN** a new turn is scored for an already-scored conversation
- **THEN** the conversation aggregate is recomputed including the new turn

### Requirement: Configurable judge model
The system SHALL use a configurable LLM model for quality scoring (`CONVERSATION_QUALITY_JUDGE_MODEL`, default "gpt-4o-mini"). The system SHALL support any LLM provider available in the existing LLM service.

#### Scenario: Default judge model
- **WHEN** no custom judge model is configured
- **THEN** the system uses "gpt-4o-mini" for quality scoring

#### Scenario: Custom judge model
- **WHEN** `CONVERSATION_QUALITY_JUDGE_MODEL` is set to "claude-3-haiku"
- **THEN** the system uses "claude-3-haiku" for quality scoring

### Requirement: Quality scoring API endpoints
The system SHALL expose REST API endpoints for querying quality scores: `GET /api/ops-center/conversations/overview` (aggregate metrics), `GET /api/ops-center/conversations/quality-distribution` (score histogram), `GET /api/ops-center/conversations/low-quality` (conversations below threshold), `GET /api/ops-center/conversations/{id}/turns` (per-turn scores for a conversation).

#### Scenario: Get conversation overview
- **WHEN** a client requests `GET /api/ops-center/conversations/overview?start_date=...&end_date=...`
- **THEN** the system returns `{total_conversations, scored_conversations, avg_quality_score, quality_distribution: {high, medium, low}}`

#### Scenario: Get quality distribution
- **WHEN** a client requests `GET /api/ops-center/conversations/quality-distribution?start_date=...&end_date=...`
- **THEN** the system returns a histogram of quality scores (buckets: 0.0–0.2, 0.2–0.4, 0.4–0.6, 0.6–0.8, 0.8–1.0)

#### Scenario: Get low-quality conversations
- **WHEN** a client requests `GET /api/ops-center/conversations/low-quality?threshold=0.5&start_date=...&end_date=...`
- **THEN** the system returns conversations with quality_score below the threshold, sorted by quality_score ascending

#### Scenario: Get turn-level scores for a conversation
- **WHEN** a client requests `GET /api/ops-center/conversations/{id}/turns`
- **THEN** the system returns all turn scores for the conversation, ordered by turn_index

### Requirement: Error handling for scoring failures
The system SHALL handle LLM scoring failures gracefully. If scoring fails for a turn, the system SHALL log the error and continue processing remaining turns. Failed turns SHALL NOT have quality scores (nullable columns).

#### Scenario: LLM call fails for one turn
- **WHEN** the LLM call fails for turn 2 of a 3-turn conversation
- **THEN** the system logs the error, skips turn 2, and continues scoring turn 3. Turn 2 has no quality score.

#### Scenario: LLM call times out
- **WHEN** the LLM call times out (30s default)
- **THEN** the system logs the timeout error and skips the turn
