## 1. Data Model & Migration

- [x] 1.1 Add `opening_remarks` (TEXT nullable) and `enable_suggestions` (BOOLEAN default true) columns to `AgentModel` in `src/hecate/models/agent.py`
- [x] 1.2 Add `opening_remarks` and `enable_suggestions` fields to `AgentCreateSchema`, `AgentUpdateSchema`, `AgentReadSchema`
- [x] 1.3 Generate Alembic migration for the two new columns
- [x] 1.4 Write tests for new agent schema fields in `tests/test_models/test_agent.py`

## 2. Suggestion Types & Prompt Templates

- [x] 2.1 Create `src/hecate/services/suggestions/types.py` with `SuggestionResult` Pydantic schema (questions: list[str], model: str, usage: dict)
- [x] 2.2 Create `src/hecate/services/suggestions/prompts.py` with `build_opening_prompt()` and `build_followup_prompt()` functions
- [x] 2.3 Write tests for prompt template functions in `tests/test_services/test_suggestions/test_prompts.py`

## 3. Suggestion Service

- [x] 3.1 Create `src/hecate/services/suggestions/__init__.py` with module docstring
- [x] 3.2 Create `src/hecate/services/suggestions/service.py` with `SuggestionService` class containing `generate_opening()` and `generate_suggestions()` methods
- [x] 3.3 Implement LLM-based suggestion generation with 2-second timeout and JSON array parsing
- [x] 3.4 Implement static fallback: extract questions from agent persona when LLM fails
- [x] 3.5 Write tests for SuggestionService in `tests/test_services/test_suggestions/test_service.py`

## 4. Conversation Service Integration

- [x] 4.1 Add `generate_opening` and `generate_suggestions` parameters to `ConversationService.chat()` method signature
- [x] 4.2 Implement `_generate_opening_remarks()` method that checks agent config and calls SuggestionService
- [x] 4.3 Implement `_generate_followup_suggestions()` method that generates suggestions after response
- [x] 4.4 Integrate opening remarks into `_complete_chat()` — return greeting with suggested_questions
- [x] 4.5 Integrate opening remarks into `_stream_chat()` — yield content then suggestions event
- [x] 4.6 Integrate follow-up suggestions into `_complete_chat()` — append suggested_questions to result
- [x] 4.7 Integrate follow-up suggestions into `_stream_chat()` — yield suggestions event before done

## 5. API Layer

- [x] 5.1 Add `generate_opening` (bool default false) and `generate_suggestions` (bool default false) to `ChatCompletionRequest`
- [x] 5.2 Add `suggested_questions` (list[str] | None) field to `ChatMessage`
- [x] 5.3 Update `create_chat_completion()` to pass new flags to ConversationService when provided
- [x] 5.4 Handle opening remarks flow in streaming mode — yield greeting content, then suggestions, then done
- [x] 5.5 Handle opening remarks flow in non-streaming mode — return greeting with suggested_questions
- [x] 5.6 Write API integration tests in `tests/test_api/test_opening_remarks.py`

## 6. Context Assembler Enhancement

- [x] 6.1 Add `suggestion_mode` parameter to `ContextAssembler.assemble()` method
- [x] 6.2 Implement opening suggestion mode — build system prompt with agent metadata
- [x] 6.3 Implement followup suggestion mode — build system prompt with last 2 turns
- [x] 6.4 Write tests for suggestion mode assembly in `tests/test_services/test_context/test_suggestion_mode.py`

## 7. Feature Catalog & Verification

- [x] 7.1 Update feature catalog `docs/features/feature-catalog.md` to mark 1.3.8 as ✅
- [x] 7.2 Run `ruff check src/hecate/ tests/` and `ruff format --check src/ tests/` — zero errors
- [x] 7.3 Run `mypy src/` — zero new errors
- [x] 7.4 Run `python -m pytest tests/ -q` — all tests passing
