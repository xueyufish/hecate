## ADDED Requirements

### Requirement: OIDCAuthProvider implements AuthProviderABC
The system SHALL define `OIDCAuthProvider(AuthProviderABC)` in `auth/oidc_provider.py` that authenticates users via OpenID Connect authorization code flow using Authlib's Starlette client.

#### Scenario: OIDC provider initialization
- **WHEN** an OIDCAuthProvider is created with `client_id`, `client_secret`, `discovery_url`, and `scope` configuration
- **THEN** the provider SHALL register an Authlib `OidcClient` that fetches IdP metadata from the discovery URL on first use

#### Scenario: Initiate OIDC login
- **WHEN** a user navigates to `/auth/sso/oidc/login`
- **THEN** the system SHALL redirect to the IdP authorization endpoint with PKCE challenge and state parameter
- **AND** the redirect URL SHALL include `client_id`, `redirect_uri`, `scope=openid profile email`, and `response_type=code`

#### Scenario: OIDC callback with valid authorization code
- **WHEN** the IdP redirects back to `/auth/sso/oidc/callback` with a valid `code` and matching `state`
- **THEN** the system SHALL exchange the code for an access token and ID token
- **AND** SHALL fetch userinfo from the IdP userinfo endpoint
- **AND** SHALL map the `sub` claim to `UserModel.sso_id` for user resolution

#### Scenario: JIT provisioning on first OIDC login
- **WHEN** the OIDC userinfo `sub` claim does not match any existing `UserModel.sso_id`
- **THEN** the system SHALL create a new UserModel with `sso_id=sub`, `email=userinfo.email`, `display_name=userinfo.name`, `auth_method="sso"`, `active=True`, and a random `hashed_password`
- **AND** SHALL issue a JWT token for the newly created user

#### Scenario: OIDC callback with invalid state
- **WHEN** the callback request has a `state` parameter that does not match the value stored in the session
- **THEN** the system SHALL return HTTP 400 with error "Invalid state parameter"

#### Scenario: OIDC callback with expired or invalid code
- **WHEN** the IdP returns an error or the code exchange fails
- **THEN** the system SHALL return HTTP 401 with error "OIDC authentication failed"

### Requirement: SAMLAuthProvider implements AuthProviderABC
The system SHALL define `SAMLAuthProvider(AuthProviderABC)` in `auth/saml_provider.py` that authenticates users via SAML 2.0 SP-initiated SSO using python3-saml.

#### Scenario: SAML provider initialization
- **WHEN** a SAMLAuthProvider is created with `sp_entity_id`, `sp_acs_url`, `idp_entity_id`, `idp_sso_url`, and `idp_x509_cert` configuration
- **THEN** the provider SHALL initialize a OneLogin_Saml2_Auth instance with SP and IdP metadata

#### Scenario: Initiate SAML login
- **WHEN** a user navigates to `/auth/sso/saml/login`
- **THEN** the system SHALL generate a SAML AuthnRequest and redirect to the IdP SSO URL

#### Scenario: SAML ACS with valid assertion
- **WHEN** the IdP POSTs a SAML response to `/auth/sso/saml/acs` with a valid signed assertion
- **THEN** the system SHALL validate the XML signature, check the assertion conditions (NotBefore, NotOnOrAfter)
- **AND** SHALL extract the `NameID` as the user identifier and map it to `UserModel.sso_id`

#### Scenario: JIT provisioning on first SAML login
- **WHEN** the SAML NameID does not match any existing `UserModel.sso_id`
- **THEN** the system SHALL create a new UserModel with `sso_id=NameID`, email from the SAML attribute statement, and `active=True`

#### Scenario: SAML ACS with invalid signature
- **WHEN** the SAML response has an invalid or missing signature
- **THEN** the system SHALL return HTTP 401 with error "SAML signature validation failed"

### Requirement: LDAPAuthProvider implements AuthProviderABC
The system SHALL define `LDAPAuthProvider(AuthProviderABC)` in `auth/ldap_provider.py` that authenticates users via LDAP bind using ldap3 with asyncio transport.

#### Scenario: LDAP provider initialization
- **WHEN** an LDAPAuthProvider is created with `server_url`, `base_dn`, `bind_dn`, `bind_password`, `search_filter`, and `use_ssl` configuration
- **THEN** the provider SHALL initialize an ldap3 connection pool with the configured server

#### Scenario: LDAP authentication with valid credentials
- **WHEN** `authenticate(token, db)` is called where token is a base64-encoded `username:password` string
- **THEN** the system SHALL bind to the LDAP server with the user's DN (resolved via search filter)
- **AND** if bind succeeds, SHALL query UserModel by `sso_id=username` or create via JIT
- **AND** SHALL return an AuthContext with `auth_method="ldap"`

#### Scenario: LDAP authentication with invalid credentials
- **WHEN** the LDAP bind fails with `INVALID_CREDENTIALS`
- **THEN** the system SHALL return `None` (auth provider chain continues to next provider)

#### Scenario: LDAP server unreachable
- **WHEN** the LDAP server is unreachable or times out
- **THEN** the system SHALL log an error and return `None` (does not block the auth chain)

### Requirement: SSO auth context method
The system SHALL extend AuthContext to support `"sso"` and `"ldap"` as valid `auth_method` values in addition to the existing `"jwt"` and `"api_key"`.

#### Scenario: AuthContext from SSO authentication
- **WHEN** a user authenticates successfully via OIDC or SAML
- **THEN** the AuthContext SHALL have `auth_method="sso"`, `user_id`, `org_id`, `workspace_id`, and `role` resolved from the user's workspace membership

#### Scenario: AuthContext from LDAP authentication
- **WHEN** a user authenticates successfully via LDAP
- **THEN** the AuthContext SHALL have `auth_method="ldap"` with the same fields as SSO

### Requirement: SSO configuration in Settings
The system SHALL add SSO provider configuration to the Settings class with fields for OIDC, SAML, and LDAP providers.

#### Scenario: OIDC configuration
- **WHEN** Settings includes `SSO_OIDC_CLIENT_ID`, `SSO_OIDC_CLIENT_SECRET`, `SSO_OIDC_DISCOVERY_URL`
- **THEN** the OIDCAuthProvider SHALL be registered in the auth resolver

#### Scenario: SAML configuration
- **WHEN** Settings includes `SSO_SAML_SP_ENTITY_ID`, `SSO_SAML_IDP_ENTITY_ID`, `SSO_SAML_IDP_SSO_URL`, `SSO_SAML_IDP_X509_CERT`
- **THEN** the SAMLAuthProvider SHALL be registered in the auth resolver

#### Scenario: LDAP configuration
- **WHEN** Settings includes `SSO_LDAP_SERVER_URL`, `SSO_LDAP_BASE_DN`, `SSO_LDAP_BIND_DN`, `SSO_LDAP_BIND_PASSWORD`
- **THEN** the LDAPAuthProvider SHALL be registered in the auth resolver

#### Scenario: No SSO configured
- **WHEN** no SSO settings are provided
- **THEN** no SSO providers SHALL be registered and existing JWT/APIKey auth continues to work
