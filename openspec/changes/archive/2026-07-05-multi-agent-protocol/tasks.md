## 1. Unified Skill Registry (2.9)

- [x] 1.1 Create `src/hecate/skill_registry/__init__.py` with public exports
- [x] 1.2 Create `src/hecate/skill_registry/types.py` — SkillRef dataclass, ResolvedSkill dataclass, SkillRefType enum (tool/skill/knowledge/workflow/agent/remote_agent)
- [x] 1.3 Create `src/hecate/skill_registry/registry.py` — SkillRegistry service with resolve(), invoke(), format_for_llm()
- [x] 1.4 Implement tool resolution (SkillRef → ToolModel lookup by name)
- [x] 1.5 Implement skill resolution (SkillRef → SkillModel lookup by name)
- [x] 1.6 Implement knowledge base resolution (SkillRef → KnowledgeBaseModel lookup by UUID)
- [x] 1.7 Implement workflow resolution (SkillRef → WorkflowModel lookup by UUID)
- [x] 1.8 Implement agent resolution (SkillRef → AgentModel lookup by UUID)
- [x] 1.9 Implement invoke() routing to EnginePort methods (tool_execute, knowledge_query, workflow_execute, agent_execute)
- [x] 1.10 Implement format_for_llm() producing XML/JSON context blocks for each skill type
- [x] 1.11 Add `skill_ids` JSON field to AgentModel (complement existing tools/skills/knowledge_base_ids)
- [x] 1.12 Create Alembic migration for agent.skill_ids column
- [x] 1.13 Add SkillRegistry to core DI container
- [x] 1.14 Create `tests/test_skill_registry/test_resolve.py` — test each ref_type resolution
- [x] 1.15 Create `tests/test_skill_registry/test_invoke.py` — test invoke routing
- [x] 1.16 Create `tests/test_skill_registry/test_format.py` — test LLM formatting
- [x] 1.17 Create `tests/test_skill_registry/test_backward_compat.py` — test agents with legacy fields still work

## 2. Agent-Workflow Mutual Embedding (2.9a)

- [x] 2.1 Add `workflow_execute()` optional method to EnginePort (default NotImplementedError)
- [x] 2.2 Implement `workflow_execute()` in AgentExecutionPort — resolve workflow, compile graph, execute via PregelRuntime
- [x] 2.3 Create `src/hecate/engine/workflow_tool.py` — WorkflowTool class wrapping workflow_id (analogous to AgentTool)
- [x] 2.4 Implement WorkflowTool.name, .description, .execute() with JSON Schema generation from Start Node variables
- [x] 2.5 Implement nesting depth tracking via context stack (max_depth=3, raise NestingDepthExceededError)
- [x] 2.6 Verify AgentWorker (NodeType.AGENT) passes channel snapshot and receives response correctly
- [x] 2.7 Create `tests/test_engine/test_workflow_tool.py` — test WorkflowTool schema generation and execution
- [x] 2.8 Create `tests/test_engine/test_nesting_depth.py` — test depth limit enforcement (depth 1, 2, 3 pass; depth 4 raises)

## 3. A2A Protocol Foundation (2.10)

- [x] 3.1 Add `a2a-sdk` to pyproject.toml `[tools]` optional dependency group
- [x] 3.2 Add A2A config to `core/config.py` (A2A_SERVER_ENABLED, A2A_SERVER_URL, A2A_AGENT_NAME, A2A_AUTH_MODE)
- [x] 3.3 Create `src/hecate/a2a/__init__.py` with public exports
- [x] 3.4 Create `src/hecate/a2a/types.py` — Pydantic models for A2A protocol objects (AgentCard, Task, TaskStatus, Artifact, Message, Part)
- [x] 3.5 Create `src/hecate/a2a/server/card.py` — AgentCard generator from Hecate config + SkillRegistry
- [x] 3.6 Create `src/hecate/a2a/server/executor.py` — AgentExecutor bridging A2A requests to EnginePort
- [x] 3.7 Create `src/hecate/a2a/server/task_store.py` — DatabaseTaskStore using async SQLAlchemy (a2a_tasks table)
- [x] 3.8 Create `src/hecate/a2a/server/streaming.py` — SSE event emitter for TaskStatusUpdateEvent / TaskArtifactUpdateEvent
- [x] 3.9 Create `src/hecate/a2a/server/handler.py` — JSON-RPC request handler (SendMessage, SendStreamingMessage, GetTask, CancelTask)
- [x] 3.10 Create `src/hecate/a2a/server/auth.py` — APIKey + HTTP Bearer auth using existing AuthProviderABC
- [x] 3.11 Create `src/hecate/a2a/server/app.py` — FastAPI router with /.well-known/agent-card.json + /a2a/ JSON-RPC endpoint
- [x] 3.12 Register A2A server routes in `src/hecate/main.py` (conditional on A2A_SERVER_ENABLED)
- [x] 3.13 Create `src/hecate/models/a2a_task.py` — A2ATaskModel ORM (id, context_id, state, status_message, artifacts, history, workspace_id)
- [x] 3.14 Create Alembic migration for a2a_tasks table
- [x] 3.15 Create `tests/test_a2a/test_server/test_card.py` — test AgentCard generation
- [x] 3.16 Create `tests/test_a2a/test_server/test_handler.py` — test JSON-RPC methods (send, get, cancel)
- [x] 3.17 Create `tests/test_a2a/test_server/test_streaming.py` — test SSE event format
- [x] 3.18 Create `tests/test_a2a/test_server/test_task_store.py` — test task lifecycle persistence
- [x] 3.19 Create `tests/test_a2a/test_server/test_auth.py` — test APIKey and Bearer auth

## 4. A2A Client (2.10)

- [x] 4.1 Create `src/hecate/a2a/client/discovery.py` — fetch and parse AgentCard from /.well-known/agent-card.json
- [x] 4.2 Create `src/hecate/a2a/client/client.py` — A2AClient with send_message(), send_streaming_message(), get_task(), cancel_task()
- [x] 4.3 Implement A2AClient auth header injection (APIKey, Bearer token)
- [x] 4.4 Implement A2AClient timeout and retry (reuse RetryStrategy pattern)
- [x] 4.5 Create `src/hecate/a2a/client/push.py` — FastAPI webhook receiver for push notifications
- [x] 4.6 Implement remote_agent SkillRef resolution in SkillRegistry (fetch AgentCard via discovery)
- [x] 4.7 Implement remote_agent SkillRef invocation in SkillRegistry (delegate to A2AClient.send_message)
- [x] 4.8 Create `tests/test_a2a/test_client/test_discovery.py` — test AgentCard fetch and parse
- [x] 4.9 Create `tests/test_a2a/test_client/test_client.py` — test A2AClient methods
- [x] 4.10 Create `tests/test_a2a/test_client/test_push.py` — test webhook receiver

## 5. Signed Agent Cards (2.10a)

- [x] 5.1 Create `src/hecate/a2a/signing.py` — ES256 key pair generation, JWS signing, RFC 8785 canonicalization
- [x] 5.2 Implement sign_agent_card(card, private_key) → card with signatures array
- [x] 5.3 Implement verify_agent_card(card, jwks_url) → bool, with JWKS fetch + cache
- [x] 5.4 Implement algorithm pinning (ES256 only, reject alg:none, RS256, HS256)
- [x] 5.5 Create `src/hecate/models/agent_card_key.py` — AgentCardKeyModel ORM (kid, private_key, public_key, algorithm, workspace_id, status, created_at, rotated_at)
- [x] 5.6 Create Alembic migration for agent_card_keys table
- [x] 5.7 Create JWKS endpoint at `/.well-known/jwks.json` returning public keys in JWK format
- [x] 5.8 Implement key rotation API (POST /api/a2a/keys/rotate) with grace period (old key served for 7 days)
- [x] 5.9 Integrate signing into AgentCard generation (sign when workspace has active key)
- [x] 5.10 Integrate verification into A2AClient discovery (verify before returning card)
- [x] 5.11 Create `tests/test_a2a/test_signing.py` — test sign/verify cycle, alg pinning, JWKS format
- [x] 5.12 Create `tests/test_a2a/test_key_rotation.py` — test rotation with grace period

## 6. Collaborative Conflict Handling (2.8)

- [x] 6.1 Extend ConflictStrategy enum with DISTRIBUTED_LOCK and NEGOTIATION strategies
- [x] 6.2 Implement distributed lock mode in ConflictResolver (async lock acquisition with TTL via Redis or DB)
- [x] 6.3 Implement negotiation-based conflict resolution (delegate to P2PNegotiator)
- [x] 6.4 Add task-level conflict detection (two agents claim same task via TaskAllocator)
- [x] 6.5 Add permission scope mismatch detection for A2A remote agents (check auth scope vs requested action)
- [x] 6.6 Add A2A-specific event types to CollaborationEventType enum (A2A_TASK_DELEGATED, A2A_TASK_RECEIVED, A2A_ARTIFACT_SENT, A2A_ARTIFACT_RECEIVED, A2A_AGENT_DISCOVERED)
- [x] 6.7 Implement A2A task ID correlation in EventBus payload metadata
- [x] 6.8 Create `tests/test_engine/test_conflict_distributed.py` — test distributed lock strategy
- [x] 6.9 Create `tests/test_engine/test_conflict_a2a.py` — test A2A-related conflict scenarios

## 7. API Layer

- [x] 7.1 Create `src/hecate/api/management/a2a.py` — A2A management API (key management, remote agent config, task listing)
- [x] 7.2 Create `src/hecate/api/management/skill_registry.py` — SkillRegistry API (list resolved skills, test invoke)
- [x] 7.3 Register A2A and SkillRegistry management routes in main.py
- [x] 7.4 Create `tests/test_api/test_a2a_management.py` — test key rotation, remote agent config
- [x] 7.5 Create `tests/test_api/test_skill_registry_api.py` — test skill listing and test invoke

## 8. Integration & Verification

- [x] 8.1 Extend AgentTool to support remote_agent targets (delegate to A2AClient.send_message)
- [x] 8.2 Extend AgentTool to support workflow targets (use WorkflowTool internally)
- [x] 8.3 Verify A2A task execution triggers guardrail hooks (PreLLMHook fires during A2A SendMessage)
- [x] 8.4 Verify A2A task appears in Full-Chain Tracing
- [x] 8.5 Verify embedded workflow triggers guardrails and tracing
- [x] 8.6 Run `ruff check src/hecate/a2a/ src/hecate/skill_registry/ tests/test_a2a/ tests/test_skill_registry/`
- [x] 8.7 Run `mypy src/hecate/a2a/ src/hecate/skill_registry/`
- [x] 8.8 Run `python -m pytest tests/test_a2a/ tests/test_skill_registry/ tests/test_engine/test_workflow_tool.py tests/test_engine/test_nesting_depth.py tests/test_engine/test_conflict_distributed.py -v`
- [x] 8.9 Run full test suite `python -m pytest tests/ -q`
