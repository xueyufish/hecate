## 1. Infrastructure Setup

- [x] 1.1 Create `src/hecate/services/context/` module with `__init__.py`
- [x] 1.2 Add `tiktoken` dependency to `pyproject.toml`
- [x] 1.3 Create `EvidenceModel` ORM model in `models/evidence.py` (evidences table: id, session_id, conversation_id, message_id, tool_name, tool_arguments JSONB, raw_content TEXT, normalized_content JSONB, is_error BOOL, importance FLOAT, source_type VARCHAR, provenance JSONB, created_at)
- [x] 1.4 Create `BudgetSnapshotModel` ORM model in `models/budget.py` (budget_snapshots table: id, session_id, total_budget INT, tokens_used INT, tokens_remaining INT, degradation_level VARCHAR, created_at)
- [x] 1.5 Create Pydantic schemas for Evidence and BudgetSnapshot (CreateSchema, ReadSchema)
- [x] 1.6 Generate Alembic migration for new tables

## 2. Budget Governance

- [x] 2.1 Implement `TokenCounter` class in `services/context/token_counter.py` — wraps tiktoken with `count_messages()` method accepting list of message dicts, returns total token count
- [x] 2.2 Implement `BudgetManager` class in `services/context/budget.py` — tracks per-session budget allocation and cumulative usage; `check_budget(session_id, messages) -> BudgetCheck` returns budget status and deficit
- [x] 2.3 Implement Level 1 degradation (DROP) — filter out messages with priority "low" from the message list
- [x] 2.4 Implement Level 2 degradation (COMPRESS) — use LLM to compress medium-priority messages into a short summary paragraph
- [x] 2.5 Implement Level 3 degradation (EMERGENCY) — replace entire history with a single emergency summary containing: original objective, key decisions, current state
- [x] 2.6 Implement `degrade(messages, deficit, priorities) -> list[dict]` orchestrator that applies levels sequentially until within budget
- [x] 2.7 Implement budget snapshot recording — after each LLM call, persist BudgetSnapshotModel with usage data

## 3. Context Assembler

- [x] 3.1 Implement `AssembledContext` dataclass in `services/context/types.py` — contains messages, tools, metadata (phase, token_count, priorities)
- [x] 3.2 Implement message priority assignment in `services/context/prioritizer.py` — assign critical/high/medium/low based on role, recency, and content type
- [x] 3.3 Implement task phase detection in `services/context/phase_detector.py` — classify recent message pattern as explore/converge/execute/verify
- [x] 3.4 Implement tool filtering by phase in `services/context/tool_filter.py` — filter tool list based on detected phase and agent's phase-to-tool mapping
- [x] 3.5 Implement task work panel construction in `services/context/work_panel.py` — for conversations > 3 turns, construct structured panel: objective + recent exchanges + latest tool result + older summary
- [x] 3.6 Implement `ContextAssembler.assemble(messages, tools, knowledge, session_meta) -> AssembledContext` — orchestrates prioritizer → phase_detector → tool_filter → work_panel → budget_check

## 4. Evidence Management

- [x] 4.1 Implement `EvidenceTracker.capture(tool_name, args, result, context) -> EvidenceRecord` — intercept tool results and create structured evidence records
- [x] 4.2 Implement evidence normalization — parse JSON outputs, wrap plain text, handle error results
- [x] 4.3 Implement provenance tracking — populate session_id, conversation_id, message_id, turn_index in each evidence record
- [x] 4.4 Implement importance scoring — default 0.5, error=0.0, boost +0.1 on re-reference (capped at 1.0)
- [x] 4.5 Implement evidence persistence — save EvidenceModel to database via async session
- [x] 4.6 Implement evidence query interface — `query(session_id, tool_name, min_importance, time_range) -> list[EvidenceRecord]`

## 5. Provider Shaping

- [x] 5.1 Implement `ProviderStrategy` ABC in `services/context/provider_shaping.py` — abstract `shape(context: AssembledContext) -> AssembledContext`
- [x] 5.2 Implement `DefaultStrategy` — pass-through, no modifications
- [x] 5.3 Implement `OpenAIStrategy` — truncate system messages > 2000 tokens, keep system message in messages array
- [x] 5.4 Implement `AnthropicStrategy` — extract system message to top-level parameter, adapt tool definitions format
- [x] 5.5 Implement strategy registry and auto-selection — `get_strategy(model: str) -> ProviderStrategy` based on model name prefix, with registration API for custom strategies

## 6. Integration

- [x] 6.1 Add `context_assemble` and `evidence_query` methods to `EnginePort` ABC (with default no-op implementations for backward compatibility)
- [x] 6.2 Modify `ConversationService._complete_chat()` to call `ContextAssembler.assemble()` before `llm_service.chat()` and wrap tool execution with `EvidenceTracker.capture()`
- [x] 6.3 Modify `ConversationService._stream_chat()` to call `ContextAssembler.assemble()` before streaming and wrap tool execution with `EvidenceTracker.capture()`
- [x] 6.4 Apply `ProviderStrategy.shape()` to the assembled context before passing to `LLMService`
- [x] 6.5 Wire `BudgetManager` into the assembly pipeline — after assembly, check budget and apply degradation if needed

## 7. Testing

- [x] 7.1 Unit tests for `TokenCounter` — verify token counting accuracy for known message sets
- [x] 7.2 Unit tests for `BudgetManager` — budget allocation, check, and three-level degradation with mock messages
- [x] 7.3 Unit tests for `ContextAssembler` — pass-through mode, phase detection, tool filtering, work panel construction
- [x] 7.4 Unit tests for `EvidenceTracker` — capture, normalization, provenance, importance scoring, query
- [x] 7.5 Unit tests for provider strategies — OpenAI truncation, Anthropic system message extraction, default pass-through
- [x] 7.6 Integration test for full pipeline: messages → assembler → budget check → provider shaping → LLM mock
- [x] 7.7 Integration test for tool execution with evidence capture and budget update
