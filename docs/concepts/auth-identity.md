# Authentication and Identity

Every request to Hecate answers two questions: **who is calling**, and **what may they touch**. The first is *authentication* — proving identity. The second is *authorization* — enforcing the [multi-tenancy hierarchy](multi-tenancy.md) and the [guardrail policies](guardrails.md) that gate every tool and LLM call.

Hecate supports two fundamental credential types — **API Keys** for machine identity and **JWTs** for human identity — plus enterprise SSO (OIDC, SAML, LDAP) and SCIM v2 for federated provisioning. This page explains the identity model and how the credential types relate. For step-by-step SSO/SCIM configuration, see the [Configure SSO and SCIM guide](../how-to/configure-sso-scim.md).

> **Boundary with Multi-Tenancy.** [Multi-Tenancy](multi-tenancy.md) answers "where does data live and who can see it" — the Organization → Workspace → User hierarchy and `workspace_id` isolation. This page answers "who is the caller and how did they prove it." The two systems are separable: you can run Hecate with identity but a single workspace, or with multi-workspace isolation under one admin token.

---

## The two credential types

| Credential | Format | Carries | Best for |
|-----------|--------|---------|----------|
| **API Key** | `hcat_*` prefix | Scope (`SYSTEM` / `WORKSPACE`), optional `org_id` / `workspace_id`, expiry | Server-to-server integration, CI/CD, automation |
| **JWT (Bearer)** | `eyJ...` (JWT) | `user_id`, `org_id`, `workspace_id`, `role`, expiry | Interactive sessions, end-user applications |

Both are accepted as `Authorization: Bearer <token>` on every API endpoint. The auth layer inspects the prefix to decide which validation path to run — `hcat_*` goes through `APIKeyAuthProvider`; everything else is parsed as a JWT.

---

## API Keys

API keys are database-backed, scoped credentials for machine-to-machine access. The `ApiKeyModel` (`models/api_key.py`) stores:

- **`key_hash`** — SHA-256 hash of the full key. The raw key is shown **only once** at creation and never persisted in plaintext.
- **`key_prefix`** — first 12 characters, stored for display so you can identify a key in the management UI without revealing it.
- **`scope`** — `SYSTEM` (cross-org platform admin) or `WORKSPACE` (single-workspace operations).
- **`org_id` / `workspace_id`** — the tenant context the key operates in (required for `WORKSPACE` scope).
- **`expires_at`** — optional expiry; expired keys are rejected.
- **`last_used_at`** — updated on each authenticated request, surfaced in the UI for key hygiene.

Keys are created via `ApiKeyService` (`services/api_key_service.py`) with the `_KEY_PREFIX = "hcat_"` convention. The returned value looks like `hcat_a1b2c3d4...` — store it immediately, because Hecate cannot recover it later.

The `APIKeyAuthProvider` (`auth/api_key_provider.py`) implements the `AuthProvider` interface: hash the incoming key, look up the hash, verify scope and expiry, and attach the key's tenant context to the request.

---

## JWT tokens

For human (interactive) identity, Hecate issues JSON Web Tokens via the `AuthService` (`services/auth/service.py`). Tokens are generated and verified by `services/auth/token.py` using the `python-jose` library:

| Token type | Lifetime | Purpose |
|-----------|----------|---------|
| **Access token** | Short-lived | Authorizes API calls; carries `user_id`, `org_id`, `workspace_id`, `role` |
| **Refresh token** | Long-lived | Exchanged for a new access token when the access token expires; no direct API authorization |

The access token's claims encode the full tenant context of the current session. When a user [switches workspaces](multi-tenancy.md#practical-implications), the `AuthService` issues a new access token with the new `workspace_id` — the same user operates under different tenant contexts in different tokens.

### Local auth

For deployments without SSO, `UserModel` (`models/user.py`) supports email + password authentication with bcrypt-hashed passwords (`enterprise/auth/password.py`). The `sso_id` and `external_id` fields are reserved for SSO and SCIM linking — they are not used by the local auth flow.

---

## Enterprise SSO

For organizations with an existing identity provider, Hecate accepts federated identity via three protocols:

| Protocol | Direction | Typical IdP |
|----------|-----------|-------------|
| **OIDC** (OAuth 2.0) | IdP → Hecate | Okta, Azure AD, Google Workspace, Keycloak, Auth0 |
| **SAML 2.0** | IdP → Hecate | AD FS, Shibboleth, OneLogin |
| **LDAP** | Hecate → Directory | OpenLDAP, Active Directory (direct bind) |

When an SSO user signs in for the first time, Hecate creates a `UserModel` row with `sso_id` set to the IdP's user identifier — this is **just-in-time (JIT) provisioning**. Subsequent sign-ins look up the user by `sso_id` and issue a Hecate JWT. From the API's perspective, an SSO-authenticated user is indistinguishable from a locally-registered one: both carry a JWT with the same claims.

The SSO endpoints (`/auth/sso/oidc/*`, `/auth/sso/saml/*`, `/auth/sso/ldap/*`) return the same `{access_token, auth_method}` response shape as local login. See the [Configure SSO and SCIM guide](../how-to/configure-sso-scim.md) for the per-protocol setup steps.

---

## SCIM v2 provisioning

SSO handles *sign-in*; **SCIM v2** handles *lifecycle*. When an admin creates, updates, or removes a user in the IdP, the IdP's SCIM client pushes the change to Hecate's `/scim/v2/Users` and `/scim/v2/Groups` endpoints:

| SCIM operation | Effect |
|---------------|--------|
| `POST /scim/v2/Users` | Create a Hecate user with `external_id` set to the SCIM user ID |
| `PATCH /scim/v2/Users/{id}` | Update attributes (name, email, active status) |
| `DELETE /scim/v2/Users/{id}` | Deactivate the user (set `active=false`) |
| `POST /scim/v2/Groups` | Create a group; members gain workspace membership |

SCIM is independent of SSO — you can use SCIM for provisioning with any SSO method, or even with no SSO at all (manually provisioned users). The combination most enterprises adopt: SSO for authentication + SCIM for deprovisioning, so disabling a user in the IdP automatically deactivates them in Hecate.

---

## The auth provider abstraction

Hecate's auth layer is pluggable via `AuthProvider`. The built-in providers cover the common cases:

| Provider | File | Handles |
|----------|------|---------|
| `APIKeyAuthProvider` | `auth/api_key_provider.py` | `hcat_*` API keys |
| JWT validation | `services/auth/token.py` | `eyJ...` bearer tokens |
| SSO handlers | `hecate_enterprise.auth.sso_routes` (enterprise wheel) | OIDC, SAML, LDAP flows |
| SCIM endpoints | `hecate_enterprise.scim` (enterprise wheel) | User and group provisioning |

For custom auth schemes (mTLS, signed request headers, proprietary tokens), implement `AuthProvider` and register it — the rest of the system treats the authenticated identity uniformly regardless of how it was established.

---

## Choosing an auth strategy

| You want to... | Use |
|----------------|-----|
| Call Hecate from a backend service or CI/CD | API Key with `WORKSPACE` scope |
| Administer the platform programmatically | API Key with `SYSTEM` scope |
| Build an interactive app with user login | Local email/password → JWT, or SSO → JWT |
| Integrate with Okta / Azure AD / Google | OIDC SSO + JIT provisioning |
| Integrate with AD FS or legacy IdPs | SAML 2.0 SSO |
| Authenticate against a directory server | LDAP direct bind |
| Auto-provision and deprovision users | SCIM v2 alongside any SSO method |
| Build a custom auth scheme | Implement `AuthProvider` |

---

## Further reading

- [Multi-Tenancy](multi-tenancy.md) — the Organization → Workspace → User hierarchy that auth tokens reference
- [Guardrails and Hooks](guardrails.md) — how authenticated identity flows into tool permission decisions
- [Configure SSO and SCIM](../how-to/configure-sso-scim.md) — step-by-step OIDC, SAML, LDAP, and SCIM setup
- [Security Architecture](../design/security-architecture.md) — full L2 auth/RBAC/audit breakdown
- [ADR-018: Zero Trust Identity Architecture](../design/adr/018-zero-trust-identity-architecture.md) — the planned two-tier (app-level + user-level) token model
- [Enterprise Foundation Design](../design/enterprise-foundation-design.md) — identity, secrets, and compliance infrastructure
