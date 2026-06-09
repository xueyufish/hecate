## 1. Data Models & Migrations

- [x] 1.1 Create `src/hecate/models/organization.py` with OrganizationModel (id, name, slug, owner_id FK, settings JSON), OrganizationCreateSchema, OrganizationUpdateSchema, OrganizationReadSchema
- [x] 1.2 Create `src/hecate/models/workspace.py` with WorkspaceModel (id, org_id FK, name, slug, settings JSON), WorkspaceCreateSchema, WorkspaceUpdateSchema, WorkspaceReadSchema
- [x] 1.3 Create `src/hecate/models/workspace_member.py` with WorkspaceMemberModel (id, user_id FK, workspace_id FK, role enum), member CRUD schemas, and WorkspaceRole enum (admin/editor/viewer)
- [x] 1.4 Create `src/hecate/models/api_key.py` with ApiKeyModel (id, name, key_hash, key_prefix, scope enum, org_id nullable FK, workspace_id nullable FK, created_by FK, last_used_at, expires_at, is_active), ApiKeyScope enum (system/workspace), key CRUD schemas
- [x] 1.5 Update `src/hecate/models/user.py`: add optional `sso_id` field (nullable String) to UserModel, add `sso_id` to UserReadSchema
- [x] 1.6 Register new models in `src/hecate/models/__init__.py` imports so conftest `Base.metadata.create_all()` picks them up
- [x] 1.7 Add FK constraints on workspace_id for all 7 existing models: AgentModel, WorkflowModel, SkillModel, ToolModel, KnowledgeBaseModel, PromptModel, MemoryBlockModel, MemoryModel, KnowledgeMemoryModel — referencing WorkspaceModel.id
- [x] 1.8 Create Alembic migration: bootstrap default org (zero-UUID), default workspace (zero-UUID), add workspace_id FK constraints on existing models

## 2. Auth Context & Dependency Injection

- [x] 2.1 Create `src/hecate/core/auth_context.py` with AuthContext dataclass (user_id, org_id, workspace_id, role, auth_method, api_key_scope), WorkspaceRole enum re-export
- [x] 2.2 Create `src/hecate/core/deps_workspace.py` with `get_auth_context()` dependency that resolves JWT or API key to AuthContext, replacing verify_api_key and get_current_user_id
- [x] 2.3 Add RBAC dependency functions: `require_workspace_admin()`, `require_workspace_editor()`, `require_workspace_viewer()` — each checks AuthContext.role against minimum required role
- [x] 2.4 Update `src/hecate/services/auth/token.py`: expand `create_access_token()` to accept and encode org_id, workspace_id, role claims; update `decode_access_token()` to extract new claims
- [x] 2.5 Update `src/hecate/services/auth/service.py`: login method resolves user's first workspace membership and includes it in token claims; add workspace list to login response
- [x] 2.6 Add `/auth/switch-workspace` endpoint in `src/hecate/api/auth.py`: validates membership, issues new tokens with updated workspace claims
- [x] 2.7 Update `src/hecate/models/user.py` schemas: add workspaces list to login response (TokenResponseSchema or new LoginResponseSchema)

## 3. API Key Management

- [x] 3.1 Create `src/hecate/services/api_key_service.py` with ApiKeyService: create_key (generate hcat_ prefix, SHA-256 hash, store), verify_key (hash lookup, update last_used_at), rotate_key, revoke_key, list_keys
- [x] 3.2 Create `src/hecate/api/management/api_keys.py` router with endpoints: POST /api/api-keys, GET /api/api-keys, GET /api/api-keys/{id}, DELETE /api/api-keys/{id}, POST /api/api-keys/{id}/rotate
- [x] 3.3 Integrate ApiKeyService into `get_auth_context()` dependency: DB key lookup before env-var fallback, deprecation warning for env-var keys
- [x] 3.4 Update `src/hecate/core/config.py`: add deprecation warning property for HECATE_API_KEYS

## 4. Organization & Workspace Services

- [x] 4.1 Create `src/hecate/services/organization_service.py` with OrganizationService: create, get, list (by owner), update, soft_delete, transfer_ownership
- [x] 4.2 Create `src/hecate/services/workspace_service.py` with WorkspaceService: create (auto-add creator as admin), get, list (by org + membership), update, soft_delete (with resource check)
- [x] 4.3 Create `src/hecate/services/workspace_member_service.py` with WorkspaceMemberService: add_member, remove_member, update_role, list_members, check_role
- [x] 4.4 Create `src/hecate/api/management/orgs.py` router with endpoints: POST /api/orgs, GET /api/orgs, GET /api/orgs/{org_id}, PATCH /api/orgs/{org_id}, DELETE /api/orgs/{org_id}, POST /api/orgs/{org_id}/transfer-ownership
- [x] 4.5 Create `src/hecate/api/management/workspaces.py` router with endpoints: POST /api/orgs/{org_id}/workspaces, GET /api/orgs/{org_id}/workspaces, GET /api/orgs/{org_id}/workspaces/{ws_id}, PATCH /api/orgs/{org_id}/workspaces/{ws_id}, DELETE /api/orgs/{org_id}/workspaces/{ws_id}
- [x] 4.6 Create workspace member endpoints: POST /api/orgs/{org_id}/workspaces/{ws_id}/members, DELETE /api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}, PATCH /api/orgs/{org_id}/workspaces/{ws_id}/members/{user_id}, GET /api/orgs/{org_id}/workspaces/{ws_id}/members

## 5. API Endpoint Migration

- [x] 5.1 Update `src/hecate/api/management/agents.py`: replace Depends(verify_api_key) with Depends(get_auth_context), add workspace_id filter to all queries, set workspace_id from auth context on create
- [x] 5.2 Update `src/hecate/api/management/workflows.py`: same pattern as agents — auth context + workspace scoping
- [x] 5.3 Update `src/hecate/api/management/skills.py`: same pattern
- [x] 5.4 Update `src/hecate/api/management/tools.py`: same pattern
- [x] 5.5 Update `src/hecate/api/management/knowledge.py`: same pattern
- [x] 5.6 Update `src/hecate/api/management/prompts.py`: same pattern
- [x] 5.7 Update `src/hecate/api/management/memory.py`: same pattern + auth context workspace_id replaces request parameter
- [x] 5.8 Update `src/hecate/api/v1/chat.py`: inject workspace_id from auth context into conversation service calls
- [x] 5.9 Register new routers in `src/hecate/main.py`: orgs, workspaces, api_keys

## 6. Service Layer Updates

- [x] 6.1 Update all service methods that accept workspace_id as parameter to receive it from auth context instead of request body: AgentService, WorkflowService, SkillService, ToolService, KnowledgeService, PromptService, MemoryService
- [x] 6.2 Update session-memory integration: ensure ConversationService passes auth context workspace_id to WorkingMemoryService, UserMemoryService, KnowledgeMemoryService calls
- [x] 6.3 Update WorkspaceService queries in all resource services to filter by authenticated workspace_id

## 7. Router Registration & Deprecation

- [x] 7.1 Keep old `verify_api_key` and `get_current_user_id` as thin wrappers around `get_auth_context()` for backward compatibility during transition
- [x] 7.2 Add deprecation logging to env-var API key path in verify_api_key

## 8. Tests

- [x] 8.1 Create `tests/test_models/test_organization.py`: test OrganizationModel creation, slug auto-generation, unique slug constraint, soft delete
- [x] 8.2 Create `tests/test_models/test_workspace.py`: test WorkspaceModel creation, org FK, slug uniqueness within org, soft delete with resource check
- [x] 8.3 Create `tests/test_models/test_workspace_member.py`: test member CRUD, unique user-workspace constraint, role enum validation, last-admin protection
- [x] 8.4 Create `tests/test_models/test_api_key.py`: test key generation format (hcat_ prefix), hash storage, prefix extraction, scope validation, expiration check
- [x] 8.5 Create `tests/test_api/test_org_api.py`: test CRUD endpoints, ownership transfer, permission enforcement (non-owner cannot update)
- [x] 8.6 Create `tests/test_api/test_workspace_api.py`: test CRUD endpoints, member management, workspace-scoped resource listing
- [x] 8.7 Create `tests/test_api/test_rbac_api.py`: test role-based access — viewer denied create, editor denied member management, admin full access
- [x] 8.8 Create `tests/test_api/test_api_key_api.py`: test key creation, rotation, revocation, verification, scope enforcement
- [x] 8.9 Create `tests/test_api/test_auth_workspace.py`: test enriched JWT claims, switch-workspace endpoint, login workspace list, refresh with workspace context
- [x] 8.10 Update `tests/test_api/test_auth.py`: adapt existing tests for new JWT claims structure (login returns workspace list, tokens have org/ws claims)
- [x] 8.11 Update `tests/conftest.py`: add dependency_overrides for get_auth_context, create helper fixtures for org, workspace, member, api_key

## 9. Verification

- [x] 9.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 9.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 9.3 Run `mypy src/` — zero errors
- [x] 9.4 Run `python -m pytest tests/ -q` — all tests pass
