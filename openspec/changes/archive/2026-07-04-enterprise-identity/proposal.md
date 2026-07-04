## Why

Enterprise customers require federated identity, automated user lifecycle management, cost governance, and centralized secret management to deploy Hecate in regulated environments. The Platform SPI (AuthProviderABC, ChannelABC) and Cost/Quota infrastructure are in place, but lack the enterprise-grade identity integrations that Fortune 500 organizations demand: SSO via OIDC/SAML/LDAP, SCIM 2.0 directory sync from Azure AD/Okta, per-org budget enforcement with forecasting, and HashiCorp Vault / AWS Secrets Manager for dynamic credential management.

## What Changes

- **SSO Auth Providers**: Implement OIDCAuthProvider, SAMLAuthProvider, and LDAPAuthProvider as AuthProviderABC subclasses. Add OAuth/OIDC authorization code flow with JIT (Just-In-Time) user provisioning. SAML SP-initiated SSO with assertion parsing. LDAP bind authentication with async ldap3.
- **SCIM 2.0 Directory Sync**: Expose `/scim/v2/Users` and `/scim/v2/Groups` endpoints compliant with RFC 7643/7644. Support Azure AD and Okta provisioning patterns: user CRUD, group membership sync, pagination, SCIM filter syntax, soft-delete via `active=false`.
- **Budget Management**: Extend QuotaModel with `org` and `agent` scope levels. Add BudgetModel for periodic budgets with forecast projections, chargeback reports, and cost anomaly detection. Integrate with CostService for real-time spend tracking and AlertService for threshold notifications.
- **Enterprise Vault Integration**: Define SecretProviderABC abstract interface. Implement HashiCorpVaultProvider, AWSSecretsManagerProvider, and AzureKeyVaultProvider. Support OAuth 2.0 token exchange (RFC 8693) for per-agent identity to vault authentication. Dynamic short-lived credentials replace static API keys in Settings.

## Capabilities

### New Capabilities

- `sso-auth`: SSO authentication via OIDC, SAML, and LDAP protocols. Implements AuthProviderABC with authorization code flow, JIT user provisioning, assertion parsing, and LDAP bind authentication. Maps external identity provider claims to local UserModel.sso_id.
- `scim-provisioning`: SCIM 2.0 directory sync endpoints for automated user lifecycle management. Compliant with RFC 7643/7644. Supports Azure AD and Okta provisioning patterns including user CRUD, group sync, pagination, filter syntax, and deprovisioning.
- `budget-management`: Per-org, per-workspace, and per-agent spending limits with hard/soft cap enforcement. Cost forecasting, chargeback reports, and anomaly detection. Extends existing QuotaService and CostService infrastructure.
- `vault-integration`: SecretProviderABC with HashiCorp Vault, AWS Secrets Manager, and Azure Key Vault backends. Per-agent identity authentication via OAuth 2.0 token exchange. Dynamic short-lived credential provisioning.

### Modified Capabilities

_(none — all new capabilities build on existing AuthProviderABC, QuotaService, CostService, and AlertService without changing their spec-level behavior)_

## Impact

- **New modules**: `src/hecate/auth/oidc_provider.py`, `src/hecate/auth/saml_provider.py`, `src/hecate/auth/ldap_provider.py`, `src/hecate/scim/`, `src/hecate/budget/`, `src/hecate/vault/`
- **Existing files modified**: `src/hecate/models/user.py` (add `external_id`, `active`, `display_name`, `given_name`, `family_name` fields for SCIM), `src/hecate/models/quota.py` (add `ORG` and `AGENT` to QuotaScope enum), `src/hecate/main.py` (register SCIM router, vault initialization)
- **New dependencies**: `authlib` (OIDC/OAuth), `python3-saml` or `xmlsec` (SAML), `ldap3` (LDAP), `scim2-models` (SCIM schema), `hvac` (HashiCorp Vault), `aiobotocore` (AWS Secrets Manager)
- **Database migrations**: User model fields, budget tables, vault config tables, SCIM group/membership tables
- **Configuration**: New settings groups for SSO providers (client_id, client_secret, discovery_url), SCIM (bearer token), Vault (backend URL, auth method), Budget (default limits, alert thresholds)
- **API surface**: New `/auth/sso/{provider}/login`, `/auth/sso/{provider}/callback`, `/scim/v2/*`, `/api/budgets/*`, `/api/vault/secrets/*` endpoints
