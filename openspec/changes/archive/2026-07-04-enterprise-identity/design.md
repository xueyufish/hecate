## Context

Hecate's Platform SPI established AuthProviderABC (JWT + APIKey built-ins), ChannelABC, and i18n SPI. The existing auth system uses a frozen AuthContext dataclass carrying `user_id`, `org_id`, `workspace_id`, `role`, `auth_method`, and `api_key_scope`. UserModel already has an `sso_id` field reserved for external identity provider linking.

The QuotaService supports `resource_type="cost"` with `workspace` and `api_key` scopes, hard/soft limits, and window-based enforcement (rolling_minute, daily, monthly). CostService provides pricing CRUD and cost aggregation from TraceModel token usage data. AlertService supports rule-based alerting with firing/resolved states.

All secrets are currently loaded from environment variables via pydantic-settings Settings class. There is no secret provider abstraction.

Enterprise customers need: (1) SSO via their existing IdP (Azure AD, Okta, LDAP directory), (2) automated user provisioning via SCIM 2.0, (3) budget controls with cost forecasting, and (4) centralized secret management via HashiCorp Vault or cloud-native secret managers.

## Goals / Non-Goals

**Goals:**
- Implement OIDC, SAML, and LDAP authentication as AuthProviderABC plugins
- Support JIT user provisioning on first SSO login (map external identity → local UserModel via sso_id)
- Expose RFC 7643/7644-compliant SCIM 2.0 endpoints for Azure AD / Okta directory sync
- Add per-org and per-agent budget enforcement with forecasting and chargeback reports
- Define SecretProviderABC with HashiCorp Vault, AWS Secrets Manager, and Azure Key Vault backends
- All new auth providers register through existing Plugin SPI / AuthProviderABC pattern
- Backward compatible: existing JWT/APIKey auth flow unchanged

**Non-Goals:**
- Multi-factor authentication (MFA/2FA) — separate feature
- Social login (Google/Facebook/GitHub consumer OAuth) — enterprise IdPs only
- Biometric authentication
- Full IdP reverse-proxy mode (Hecate as SAML IdP, not just SP)
- Budget-based model routing (route to cheaper models when budget low) — future enhancement
- Secrets encryption at rest in database (FERNET_KEY already handles PII encryption)
- SCIM outbound sync (Hecate pushing users TO external IdP) — inbound only

## Decisions

### Decision 1: OIDC via Authlib (not custom implementation)

**Choice**: Use `authlib` library for OIDC/OAuth 2.0 client.

**Rationale**: Authlib is the de facto standard for Python OAuth/OIDC, supports async, handles token refresh, PKCE, and discovery documents. Building a custom OIDC client would require implementing JWK validation, discovery parsing, token exchange, and refresh logic — all solved by Authlib.

**Alternatives considered**:
- `itsdangerous` + manual JWT validation — too low-level, no discovery support
- `oauthlib` — less maintained, weaker async support
- `authlib.integrations.starlette_client` — native FastAPI/Starlette integration

### Decision 2: SAML via SAML python3-saml adapter (not pysaml2 directly)

**Choice**: Use `python3-saml` (SAML toolkit by OneLogin) wrapped in an async-compatible adapter.

**Rationale**: python3-saml handles XML signing, certificate validation, ACS endpoint processing, and IdP metadata parsing. It is the most widely used SAML library for Python. Direct pysaml2 is lower-level and requires more boilerplate. The library is sync, so we wrap calls in `run_in_executor` for async compatibility.

**Alternatives considered**:
- `pysaml2` — more flexible but harder to configure
- `mammoth-saml` — Laravel-only
- Custom XML signing — security risk

### Decision 3: LDAP via ldap3 with asyncio transport

**Choice**: Use `ldap3` library with `asyncio` event loop.

**Rationale**: ldap3 is the standard Python LDAP library, supports connection pooling, async operation via `asyncio.get_event_loop()`, and all LDAP server types (Active Directory, OpenLDAP, FreeIPA). Search filter syntax maps cleanly to LDAP queries.

**Alternatives considered**:
- `ldaptor` — outdated, poor async support
- `aioldap` — abandoned

### Decision 4: SCIM 2.0 via scim2-models (Pydantic v2 native)

**Choice**: Use `scim2-models` for SCIM schema definitions, validation, and filter parsing.

**Rationale**: scim2-models provides full Pydantic v2 models for User, Group, ListResponse, PatchOp, and ServiceProviderConfig. Context-aware serialization handles request/response differences. Built-in ETag support. FastAPI integration via `Annotated[User, Context.RESOURCE_CREATION_REQUEST]`. This avoids hand-crafting SCIM JSON schemas and filter parsers.

**Alternatives considered**:
- Custom Pydantic models — reinventing the wheel, high risk of RFC non-compliance
- `django-scim2` — Django-specific, not FastAPI compatible
- `scim2-filter-parser` only — just the filter parser, no models

### Decision 5: Budget extends QuotaModel, not new table

**Choice**: Extend existing QuotaScope enum with `ORG` and `AGENT` values. Add BudgetForecastModel for forecast projections and chargeback. Reuse QuotaService for enforcement.

**Rationale**: QuotaModel already supports `resource_type="cost"`, hard/soft limits, window types, and enforcement modes. Adding `org` and `agent` scopes is a 2-line enum change. Budget forecasting ( projecting future spend based on historical trends) is a new model because it needs daily snapshots. Chargeback reports are read-only views over CostService data.

**Alternatives considered**:
- Separate BudgetModel table — duplicates QuotaModel, causes confusion about which to use
- Compute forecasts on-the-fly from TraceModel — expensive for large datasets, no historical snapshots

### Decision 6: SecretProviderABC follows AuthProviderABC pattern

**Choice**: Define `SecretProviderABC` in `src/hecate/vault/provider.py` with `name`, `description`, `get_secret(path) → str`, `get_dynamic_credentials(role) → dict` abstract methods. Built-in providers: HashiCorpVaultProvider, AWSSecretsManagerProvider, AzureKeyVaultProvider.

**Rationale**: Follows the same ABC + built-in provider pattern established by AuthProviderABC and ChannelABC. The vault resolver iterates registered providers in priority order. Dynamic credentials (Vault database engine, AWS STS) return short-lived credentials that replace static API keys.

**Alternatives considered**:
- Settings-only secrets (current) — no dynamic credentials, no central rotation
- External sidecar (Vault Agent) — infrastructure complexity, not self-contained

### Decision 7: SSO user provisioning via JIT (Just-In-Time)

**Choice**: On first successful SSO authentication, auto-create a UserModel with `sso_id` set to the external identity provider's subject claim. No pre-registration required.

**Rationale**: JIT provisioning is the standard pattern for enterprise SSO. Users authenticate via their IdP, and Hecate creates a local user record on first login with `hashed_password` set to a random value (SSO users never use password auth). The `auth_method` in AuthContext is set to `"sso"`.

**Alternatives considered**:
- Admin-manual user creation before SSO — operational burden
- SCIM-only provisioning — requires SCIM setup before SSO works (not all IdPs support SCIM)

### Decision 8: SCIM deprovisioning = soft delete (active=false)

**Choice**: SCIM DELETE sets `UserModel.active = False` (new field). Users with `active=False` cannot authenticate. Hard delete is not performed automatically.

**Rationale**: Soft delete preserves audit trail, allows reactivation when IdP reassigns the user, and is the recommended pattern per Azure AD and Okta documentation. Add `active` boolean field to UserModel (default True).

**Alternatives considered**:
- Hard delete on SCIM DELETE — loses audit trail, breaks foreign key references
- Configurable per IdP — over-engineering for initial release

## Risks / Trade-offs

- **[SAML XML signature validation]** → Use python3-samel's built-in signature validation; never disable it; add integration test with signed assertions
- **[LDAP credentials in config]** → Store LDAP bind DN/password in SecretProvider (not plaintext Settings); fall back to env var if vault not configured
- **[SCIM filter injection]** → Use scim2-models parser (not string interpolation); validate all filter inputs
- **[Vault availability]** → If vault is unreachable, fall back to cached secrets with TTL; log warning; never crash on vault connection failure
- **[Budget enforcement latency]** → Quota check is already in middleware (fast); forecast computation is background job, not request path
- **[SCIM token security]** → SCIM endpoints use separate bearer token (not JWT); token stored in Settings/SecretProvider; rate-limit SCIM endpoints
- **[JIT provisioning data quality]** → IdP may send incomplete user data; map only `email`, `display_name`, `given_name`, `family_name`; log warning for missing fields

## Migration Plan

1. **Phase 1: User model expansion** — Add `active`, `external_id`, `display_name`, `given_name`, `family_name` fields to UserModel. Alembic migration. All existing users get `active=True`.
2. **Phase 2: SSO providers** — Add OIDC/SAML/LDAP providers. New `/auth/sso/{provider}/login` and `/auth/sso/{provider}/callback` endpoints. Existing JWT/APIKey auth unchanged.
3. **Phase 3: SCIM endpoints** — Add `/scim/v2/*` routes. Separate SCIM bearer token auth. No impact on existing auth.
4. **Phase 4: Budget extension** — Extend QuotaScope enum (additive, backward compatible). Add BudgetForecastModel. New budget API endpoints.
5. **Phase 5: Vault integration** — Add SecretProviderABC. Settings fallback preserved. No migration needed (Settings still works).

**Rollback**: Each phase is independent. SSO providers can be disabled by removing from auth resolver. SCIM endpoints can be unmounted. Budget scopes are additive. Vault can fall back to Settings.

## Open Questions

- Should SSO providers support **multi-tenant IdP configuration** (different OIDC client per workspace)? Initial implementation: platform-level config only, per-workspace IdP config is a future enhancement.
- Should budget forecasts use **simple linear projection** or **ARIMA/time-series model**? Initial: simple linear (average daily spend × remaining days). Advanced forecasting is future work.
- Should vault secrets be **cached in memory** or always fetched on-demand? Initial: cache with configurable TTL (default 5 minutes). Dynamic credentials are never cached.
