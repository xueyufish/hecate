## 1. Core Engine - Data Types

- [x] 1.1 Create `src/hecate/services/security/dlp/result.py` with `DLPAction` (StrEnum: ALLOW/BLOCK/MASK/AUDIT), `DLPFinding` (dataclass), `DLPResult` (dataclass)
- [x] 1.2 Add unit tests for DLPAction severity ordering (BLOCK > MASK > AUDIT > ALLOW), DLPFinding fields, DLPResult state combinations

## 2. Core Engine - Recognizer ABC + Registry

- [x] 2.1 Create `src/hecate/services/security/dlp/recognizer.py` with `DLPRecognizer` ABC (name, supported_entities, abstract analyze()) and `DLPRecognizerRegistry` (register/unregister/analyze with deduplication)
- [x] 2.2 Add unit tests for ABC instantiation rules, registry deduplication of overlapping findings, entity-type filtering

## 3. Core Engine - Built-in Recognizers

- [x] 3.1 Create `src/hecate/services/security/dlp/recognizers/regex.py` with `RegexRecognizer(DLPRecognizer)` - port 5 existing PII patterns from `PIIAnonymizer.PATTERNS`, add Luhn validation for credit_card, CHINA_ID_CARD regex pattern
- [x] 3.2 Create `src/hecate/services/security/dlp/recognizers/secrets.py` with `SecretsRecognizer(DLPRecognizer)` - wrap detect-secrets library, map AWS_ACCESS_KEY/GCP_SERVICE_KEY/PRIVATE_KEY/JWT_TOKEN/GITHUB_TOKEN
- [x] 3.3 Create `src/hecate/services/security/dlp/recognizers/presidio.py` with `PresidioRecognizer(DLPRecognizer)` - optional import, lazy model load, Presidio AnalyzerEngine wrapper
- [x] 3.4 Create `src/hecate/services/security/dlp/recognizers/dictionary.py` with `DictionaryRecognizer(DLPRecognizer)` - exact match with case_sensitive option
- [x] 3.5 Add unit tests for each recognizer: RegexRecognizer (positive/negative/Luhn), SecretsRecognizer (mocked detect-secrets), PresidioRecognizer (skip if not installed), DictionaryRecognizer (case sensitivity)

## 4. Core Engine - Policy Resolver

- [x] 4.1 Create `src/hecate/services/security/dlp/policy.py` with `DLPPolicyResolver` implementing 4-level scope lookup (agent > workspace > org > default), wildcards, is_locked enforcement
- [x] 4.2 Add unit tests for all scope combinations (agent wins over workspace wins over org wins over default), is_locked blocks lower-level override, wildcard matching, no-rule-found returns ALLOW

## 5. Core Engine - Scanner Orchestrator

- [x] 5.1 Create `src/hecate/services/security/dlp/scanner.py` with `DLPScanner` (registry + policy), scan(text, direction, context) → DLPResult, severity ranking (most restrictive action wins)
- [x] 5.2 Add unit tests for scanner orchestration: empty findings → ALLOW, single MASK, multiple findings with severity ranking (BLOCK wins over MASK wins over AUDIT)

## 6. Core Engine - Streaming Wrapper

- [x] 6.1 Create `src/hecate/services/security/dlp/streaming.py` with `StreamingDLPWrapper` - buffer management (300 char threshold, 10 char overlap), process_chunk() returning content or None (BLOCK), finalize() running final scan and reporting correction needs
- [x] 6.2 Add unit tests for streaming: threshold trigger, overlap retention, BLOCK stops stream, MASK marks for correction, AUDIT continues

## 7. Core Engine - Registry Factory

- [x] 7.1 Create `src/hecate/services/security/dlp/registry_factory.py` with `DLPRegistryFactory.create(db_session, org_id, workspace_id) -> DLPRecognizerRegistry` - loads built-in recognizers + custom regex + custom dictionaries from DB
- [x] 7.2 Add unit tests for factory: load built-ins, load custom regex, load custom dictionaries, disabled entities skipped

## 8. Built-in Default Rules

- [x] 8.1 Create `src/hecate/services/security/dlp/defaults.py` with `DEFAULT_RULES` list: secrets (5 types, BLOCK, is_locked=True), PII (SSN/CREDIT_CARD/CHINA_ID_CARD, MASK), context (EMAIL/PHONE/IP_ADDRESS, AUDIT)
- [x] 8.2 Create `create_default_policies_for_org(db_session, org_id)` function - idempotent insert of default rules
- [x] 8.3 Add unit tests for default rules loading and idempotency

## 9. Data Models + Alembic Migration

- [x] 9.1 Create `src/hecate/models/dlp.py` with `DLPPolicyModel` (org_id, workspace_id, agent_id, entity_type, direction, action, mask_format, is_locked, enabled, timestamps, indexes)
- [x] 9.2 Add Pydantic schemas: `DLPPolicyCreateSchema`, `DLPPolicyUpdateSchema`, `DLPPolicyReadSchema`, `DLPPolicyQuerySchema`
- [x] 9.3 Create `DLPCustomRegexModel` with Pydantic schemas
- [x] 9.4 Create `DLPDictionaryModel` with Pydantic schemas (terms as JSONB)
- [x] 9.5 Generate Alembic migration: `alembic/versions/xxxx_add_dlp_models.py` (3 new tables)
- [x] 9.6 Run migration in test environment, verify schema (verified via Base.metadata.create_all in conftest; alembic apply needs PostgreSQL)

## 10. Service Layer + REST API

- [x] 10.1 Create `src/hecate/services/security/dlp/service.py` with `DLPService` - CRUD for policies/custom-regex/dictionaries, policy resolution with scope lookup, dry-run scan
- [x] 10.2 Create `src/hecate/api/management/dlp.py` with REST endpoints:
  - `POST /api/v1/dlp/policies` (create)
  - `GET /api/v1/dlp/policies` (list with filters)
  - `GET /api/v1/dlp/policies/{id}` (read)
  - `PUT /api/v1/dlp/policies/{id}` (update)
  - `DELETE /api/v1/dlp/policies/{id}` (soft delete)
  - `POST /api/v1/dlp/scan/test` (dry-run)
  - `GET /api/v1/dlp/entities` (list known types)
  - `POST /api/v1/dlp/custom-regex` + GET + PUT + DELETE
  - `POST /api/v1/dlp/dictionaries` + GET + PUT + DELETE
- [x] 10.3 Add unit tests for service layer (CRUD, scope resolution) and API endpoints (HTTP integration tests)

## 11. EgressFilter ABC + DLPEgressFilter

- [x] 11.1 Create `src/hecate/services/security/egress.py` with `EgressFilter` ABC, `EgressAction` enum (ALLOW/BLOCK/MODIFIED), `EgressResult` dataclass
- [x] 11.2 Create `DLPEgressFilter(EgressFilter)` - wraps DLPScanner, handles text/non-text, maps DLPAction to EgressAction
- [x] 11.3 Add unit tests for EgressFilter ABC, DLPEgressFilter text scanning, non-text passthrough with audit, BLOCK/MASK/AUDIT mappings

## 12. MCP Client Integration

- [x] 12.1 Modify `src/hecate/services/mcp/client.py` - add `egress_filters: list[EgressFilter] | None` constructor parameter, store server_url for context
- [x] 12.2 Modify `call_tool()` method - after extracting text content, pass through egress_filters chain, stop on first BLOCK, write audit_data to SecurityFindingModel
- [x] 12.3 Add integration tests: MCP response with EMAIL → MASK works, MCP response with AWS_KEY → BLOCK works, no filters → backward compatible, non-text content → passthrough with audit

## 13. GuardrailHook Integration - Input

- [x] 13.1 Modify `src/hecate/services/security/hooks/input_security.py` - accept DLPScanner in constructor, use DLPScanner.scan(direction="llm_input") for secrets detection when available, fall back to LLMGuardScanner Secrets when not
- [x] 13.2 Keep PII anonymization in InputSecurityHook (boundary 1 mechanism, not DLP)
- [x] 13.3 Update existing tests to inject DLPScanner mock

## 14. GuardrailHook Integration - Output

- [x] 14.1 Modify `src/hecate/services/security/hooks/output_security.py` - accept DLPScanner, add DLP scan AFTER deanonymization step (boundary 2)
- [x] 14.2 DLP BLOCK returns `GuardrailResult(action=BLOCK)` instead of deanonymized response (no content leak)
- [x] 14.3 DLP MASK returns `GuardrailResult(action=SANITIZE)` with masked modified_data
- [x] 14.4 DLP AUDIT returns deanonymized response unchanged but records SecurityFinding
- [x] 14.5 Update existing tests

## 15. GuardrailHook Integration - Tool Result

- [x] 15.1 Modify `src/hecate/services/security/hooks/tool_result_security.py` - accept DLPScanner, delegate to DLPScanner.scan(direction="tool_output") instead of hardcoded PIIAnonymizer.PATTERNS
- [x] 15.2 Keep PII storage mode configuration (mask_only / mask_and_encrypt)
- [x] 15.3 Update existing tests

## 16. DI Wiring + Configuration

- [x] 16.1 Modify `src/hecate/core/config.py` - add `DLP_ENABLED`, `DLP_STREAM_ENABLED`, `DLP_STREAM_BUFFER_SIZE`, `DLP_STREAM_OVERLAP`, `DLP_STREAM_FINAL_SCAN`, `DLP_STREAM_MASK_CORRECTION` settings
- [x] 16.2 Modify `src/hecate/main.py` lifespan - create DLPScanner (with RegistryFactory loading from DB), create DLPEgressFilter, inject into MCP client factory and hook factories
- [x] 16.3 Modify `pyproject.toml` - add `[security]` extra: `presidio-analyzer>=2.2`, `presidio-anonymizer>=2.2`, `detect-secrets>=1.5`, `spacy>=3.7,<3.8`
- [x] 16.4 Add `python -m spacy download en_core_web_lg` to security extra install docs

## 17. SecurityFinding Feedback Endpoint

- [x] 17.1 Modify `src/hecate/api/security_findings.py` - add `POST /api/v1/security/findings/{id}/feedback` endpoint
- [x] 17.2 Endpoint updates `metadata_.feedback`, `metadata_.feedback_user`, `metadata_.feedback_comment` on the finding
- [x] 17.3 Add unit tests for feedback endpoint (true_positive / false_positive)

## 18. End-to-End Verification

- [x] 18.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/` - must pass with 0 errors
- [x] 18.2 Run `python -m pytest tests/ -q` - all tests must pass (existing 1713 + new ~40-50)
- [x] 18.3 Run `pre-commit run --all-files` - all hooks pass
- [x] 18.4 Integration test: create agent with default guardrail config, trigger LLM call with secrets in tool response, verify BLOCK action applied, verify SecurityFinding created
- [x] 18.5 Integration test: connect MCP server, call tool, verify response scanned and masked if EMAIL detected
- [x] 18.6 Integration test: multi-tenant - org A sets EMAIL→BLOCK locked, workspace B sets EMAIL→ALLOW, verify BLOCK wins