## 1. User Model Expansion (scim-provisioning spec)

- [x] 1.1 Add `external_id` (String 255, nullable, indexed), `display_name` (String 255, nullable), `given_name` (String 128, nullable), `family_name` (String 128, nullable), `active` (Boolean, default True) fields to `src/hecate/models/user.py` UserModel
- [x] 1.2 Update UserReadSchema to include the new fields
- [x] 1.3 Create Alembic migration for new user fields (all existing users get `active=True`)
- [x] 1.4 Update auth resolver and all auth providers to reject users with `active=False`

## 2. SSO Auth Providers — OIDC (sso-auth spec)

- [x] 2.1 Add `authlib` to `[security]` optional dependency group in `pyproject.toml`
- [x] 2.2 Add OIDC settings to `src/hecate/core/config.py`: `SSO_OIDC_CLIENT_ID`, `SSO_OIDC_CLIENT_SECRET`, `SSO_OIDC_DISCOVERY_URL`, `SSO_OIDC_SCOPE` (default "openid profile email")
- [x] 2.3 Create `src/hecate/auth/oidc_provider.py` with `OIDCAuthProvider(AuthProviderABC)` using Authlib Starlette integration — authorization code flow with PKCE, discovery document parsing, userinfo endpoint query
- [x] 2.4 Add JIT provisioning logic: on first OIDC login, create UserModel with `sso_id=sub`, `email`, `display_name`, random `hashed_password`, `active=True`
- [x] 2.5 Create SSO login/callback routes in `src/hecate/auth/sso_routes.py`: `GET /auth/sso/oidc/login` (redirect to IdP), `GET /auth/sso/oidc/callback` (code exchange + JWT issuance)
- [x] 2.6 Register OIDCAuthProvider in `src/hecate/auth/registration.py` when OIDC settings are present
- [x] 2.7 Create `tests/test_auth/test_oidc_provider.py` — test provider initialization, JIT provisioning logic, state validation, error handling

## 3. SSO Auth Providers — SAML (sso-auth spec)

- [x] 3.1 Add `python3-saml` to `[security]` optional dependency group in `pyproject.toml`
- [x] 3.2 Add SAML settings to config: `SSO_SAML_SP_ENTITY_ID`, `SSO_SAML_SP_ACS_URL`, `SSO_SAML_IDP_ENTITY_ID`, `SSO_SAML_IDP_SSO_URL`, `SSO_SAML_IDP_X509_CERT`
- [x] 3.3 Create `src/hecate/auth/saml_provider.py` with `SAMLAuthProvider(AuthProviderABC)` wrapping python3-saml — AuthnRequest generation, ACS assertion parsing, signature validation
- [x] 3.4 Add SAML routes to `src/hecate/auth/sso_routes.py`: `GET /auth/sso/saml/login`, `POST /auth/sso/saml/acs`
- [x] 3.5 Register SAMLAuthProvider in registration when SAML settings are present
- [x] 3.6 Create `tests/test_auth/test_saml_provider.py` — test AuthnRequest generation, assertion validation, JIT provisioning

## 4. SSO Auth Providers — LDAP (sso-auth spec)

- [x] 4.1 Add `ldap3` to `[security]` optional dependency group in `pyproject.toml`
- [x] 4.2 Add LDAP settings to config: `SSO_LDAP_SERVER_URL`, `SSO_LDAP_BASE_DN`, `SSO_LDAP_BIND_DN`, `SSO_LDAP_BIND_PASSWORD`, `SSO_LDAP_SEARCH_FILTER` (default "(uid={})"), `SSO_LDAP_USE_SSL` (default True)
- [x] 4.3 Create `src/hecate/auth/ldap_provider.py` with `LDAPAuthProvider(AuthProviderABC)` using ldap3 asyncio transport — user search via filter, bind authentication, connection error handling
- [x] 4.4 Add LDAP JIT provisioning: create UserModel on first successful LDAP bind with `sso_id=username`, `email` from LDAP mail attribute
- [x] 4.5 Register LDAPAuthProvider in registration when LDAP settings are present
- [x] 4.6 Create `tests/test_auth/test_ldap_provider.py` — test bind success/failure, server unreachable handling, JIT provisioning

## 5. AuthContext Extension (sso-auth spec)

- [x] 5.1 Update `src/hecate/core/auth_context.py` AuthContext `auth_method` type to `Literal["jwt", "api_key", "sso", "ldap"]`
- [x] 5.2 Update `src/hecate/auth/resolver.py` to handle SSO and LDAP auth methods in the resolver chain
- [x] 5.3 Update `src/hecate/auth/registration.py` to register all configured SSO providers
- [x] 5.4 Register SSO routes in `src/hecate/main.py`

## 6. SCIM 2.0 Core (scim-provisioning spec)

- [x] 6.1 Add `scim2-models` to `[security]` optional dependency group in `pyproject.toml`
- [x] 6.2 Add SCIM settings to config: `SCIM_BEARER_TOKEN`, `SCIM_ENABLED` (default False)
- [x] 6.3 Create `src/hecate/scim/__init__.py` with public exports
- [x] 6.4 Create `src/hecate/scim/models.py` — SCIM User/Group mappers between scim2-models and UserModel, including attribute mapping functions (to_scim_user, from_scim_user)
- [x] 6.5 Create `src/hecate/scim/auth.py` — SCIM bearer token authentication dependency for FastAPI
- [x] 6.6 Create `src/hecate/scim/filter_parser.py` — translate SCIM filter syntax (eq, co, sw, and) to SQLAlchemy queries on UserModel

## 7. SCIM 2.0 User Endpoints (scim-provisioning spec)

- [x] 7.1 Create `src/hecate/scim/users.py` with SCIM user endpoints: POST /scim/v2/Users (create), GET /scim/v2/Users (list+filter+paginate), GET /scim/v2/Users/{id}, PUT /scim/v2/Users/{id} (replace), PATCH /scim/v2/Users/{id} (partial update + active=false deprovision), DELETE /scim/v2/Users/{id} (soft delete)
- [x] 7.2 Implement SCIM error response format (RFC 7644 §3.12) with `schemas`, `status`, `scimType`, `detail` fields
- [x] 7.3 Implement pagination with `startIndex` (1-based), `count`, `totalResults` per RFC 7644 §3.4.2.4
- [x] 7.4 Implement ETag support for optimistic concurrency (If-Match header on PUT/PATCH/DELETE)

## 8. SCIM 2.0 Group + Discovery Endpoints (scim-provisioning spec)

- [x] 8.1 Create `src/hecate/scim/groups.py` with SCIM group endpoints: POST, GET (list), GET (single), PATCH (membership), DELETE — maps to workspace teams/roles
- [x] 8.2 Create `src/hecate/scim/discovery.py` — ServiceProviderConfig, Schemas, ResourceTypes endpoints
- [x] 8.3 Register SCIM router in `src/hecate/main.py` when SCIM is enabled
- [x] 8.4 Create `tests/test_scim/test_users.py` — test CRUD, filter, pagination, deprovisioning, error responses
- [x] 8.5 Create `tests/test_scim/test_groups.py` — test group CRUD and membership sync
- [x] 8.6 Create `tests/test_scim/test_discovery.py` — test ServiceProviderConfig, Schemas, ResourceTypes responses

## 9. Budget — Quota Extension (budget-management spec)

- [x] 9.1 Add `ORG = "org"` and `AGENT = "agent"` to `QuotaScope` enum in `src/hecate/models/quota.py`
- [x] 9.2 Update `src/hecate/services/quota_service.py` — `check_quota` and `record_usage` methods already accept scope/scope_id generically; verify org/agent scopes work end-to-end
- [x] 9.3 Add cost recording hook: after LLM invocation in LLMWorker or WorkflowExecutionService, call `QuotaService.record_usage(resource_type="cost", ...)` for org, workspace, and agent scopes

## 10. Budget — Forecast + Service (budget-management spec)

- [x] 10.1 Create `src/hecate/models/budget.py` with `BudgetForecastModel(BaseModel)` — fields: `scope` (String 16), `scope_id` (UUID), `date` (Date), `daily_cost` (Float), `daily_input_tokens` (Integer), `daily_output_tokens` (Integer), `workspace_id` (UUID)
- [x] 10.2 Create Alembic migration for BudgetForecastModel table
- [x] 10.3 Create `src/hecate/budget/__init__.py` with public exports
- [x] 10.4 Create `src/hecate/budget/budget_service.py` — BudgetService with: `get_utilization(scope, scope_id)`, `forecast_remaining(scope, scope_id)` using 7-day average, `create_chargeback(scope, scope_id, group_by, start, end)` delegating to CostService
- [x] 10.5 Add scheduled task for daily forecast snapshot — record BudgetForecastModel for each org/workspace with daily cost from CostService
- [x] 10.6 Create `tests/test_budget/test_budget_service.py` — test utilization, forecast projection, chargeback grouping

## 11. Budget — API Endpoints (budget-management spec)

- [x] 11.1 Create `src/hecate/api/management/budget.py` — REST endpoints: POST /api/budgets, GET /api/budgets, PUT /api/budgets/{id}, DELETE /api/budgets/{id}, GET /api/budgets/{id}/status (with forecast), GET /api/budgets/chargeback
- [x] 11.2 Register budget router in `src/hecate/main.py`
- [x] 11.3 Create `tests/test_api/test_budget_api.py` — test budget CRUD, status with forecast, chargeback report

## 12. Vault — SecretProviderABC + Built-ins (vault-integration spec)

- [x] 12.1 Create `src/hecate/vault/__init__.py` with public exports
- [x] 12.2 Create `src/hecate/vault/provider.py` — `SecretProviderABC` with `name`, `description` properties, `get_secret(path)`, `get_dynamic_credentials(role)`, `health_check()` abstract methods
- [x] 12.3 Add `hvac` to `[security]` optional dependency group
- [x] 12.4 Create `src/hecate/vault/hcvault_provider.py` — HashiCorpVaultProvider using hvac.Client, KV v2 read, database engine dynamic credentials, health check, AppRole + token auth support
- [x] 12.5 Add `aiobotocore` to `[security]` optional dependency group
- [x] 12.6 Create `src/hecate/vault/aws_provider.py` — AWSSecretsManagerProvider using aiobotocore, GetSecretValue, STS AssumeRole for dynamic credentials
- [x] 12.7 Add `azure-keyvault-secrets` + `azure-identity` to `[security]` optional dependency group
- [x] 12.8 Create `src/hecate/vault/azure_provider.py` — AzureKeyVaultProvider using SecretClient + DefaultAzureCredential

## 13. Vault — Resolver + Registration (vault-integration spec)

- [x] 13.1 Add vault settings to `src/hecate/core/config.py`: `VAULT_URL`, `VAULT_TOKEN` (or `VAULT_ROLE_ID`+`VAULT_SECRET_ID`), `VAULT_MOUNT_POINT` (default "secret"), `AWS_SECRETS_REGION`, `AWS_SECRETS_ACCESS_KEY_ID`, `AWS_SECRETS_SECRET_ACCESS_KEY`, `AZURE_KEYVAULT_URL`, `VAULT_CACHE_TTL` (default 300), `VAULT_FALLBACK_TO_SETTINGS` (default True)
- [x] 13.2 Create `src/hecate/vault/resolver.py` — `resolve_secret(path)` with provider iteration, in-memory cache with TTL, Settings fallback; `resolve_dynamic_credentials(role)` without caching
- [x] 13.3 Create `src/hecate/vault/registration.py` — `register_secret_providers(registry)` that creates and registers configured providers as Plugin SPI entries
- [x] 13.4 Register vault initialization in `src/hecate/main.py` startup
- [x] 13.5 Create `tests/test_vault/test_provider.py` — test SecretProviderABC abstractness, HashiCorpVaultProvider initialization (mocked hvac), health check
- [x] 13.6 Create `tests/test_vault/test_resolver.py` — test secret resolution with caching, fallback to Settings, dynamic credentials without cache

## 14. Integration and Final Verification

- [x] 14.1 Update `src/hecate/auth/__init__.py` to export new SSO providers (OIDCAuthProvider, SAMLAuthProvider, LDAPAuthProvider)
- [x] 14.2 Update `src/hecate/plugin/spi/__init__.py` to export SecretProviderABC
- [x] 14.3 Run full verification: `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
- [x] 14.4 Fix any lint, type, or test failures
