# How to Configure SSO and SCIM

> Wire up enterprise identity for Hecate — OIDC, SAML, or LDAP for sign-in; SCIM v2 for automated user and group provisioning.

Hecate supports three SSO protocols for authentication and one provisioning protocol for lifecycle management:

| Protocol | Direction | Purpose |
|----------|-----------|---------|
| **OIDC** (OAuth 2.0) | IdP → Hecate | Single sign-on via Okta, Azure AD, Google Workspace, Keycloak, Auth0 |
| **SAML 2.0** | IdP → Hecate | Single sign-on via AD FS, Shibboleth, OneLogin |
| **LDAP** | Hecate → Directory | Direct bind authentication (OpenLDAP, Active Directory) with JIT provisioning |
| **SCIM v2** | IdP → Hecate | Automated user/group provisioning and deprovisioning |

Pick one SSO method. SCIM is independent and works alongside any SSO.

---

## Prerequisites

- Hecate running with a configured database (see [Quickstart](../getting-started/quickstart.md))
- For OIDC/SAML: admin access to your IdP to register a new application
- For LDAP: network connectivity from Hecate to the LDAP server
- For SCIM: ability to provision a bearer token

---

## Part 1 — Configure an SSO provider

### Option A: OIDC (recommended)

Most modern IdPs support OIDC. Use this for new deployments.

#### Step 1 — Register Hecate at your IdP

Create a new OIDC application with these settings:

| Setting | Value |
|---------|-------|
| Application type | Web application |
| Redirect URI | `https://hecate.example.com/auth/sso/oidc/callback` |
| Grant type | Authorization Code |
| Scopes | `openid profile email` |

The IdP will issue a **Client ID** and **Client Secret**, and a **Discovery URL** (typically `https://idp.example.com/.well-known/openid-configuration`).

#### Step 2 — Configure Hecate

Add to `.env`:

```dotenv
# .env
SSO_OIDC_CLIENT_ID=<your-client-id>
SSO_OIDC_CLIENT_SECRET=<your-client-secret>
SSO_OIDC_DISCOVERY_URL=https://idp.example.com/.well-known/openid-configuration
SSO_OIDC_SCOPE=openid profile email
```

Restart Hecate. Verify the discovery document is reachable from the Hecate container — many IdPs block internal subnets:

```bash
docker compose exec hecate curl -fsS https://idp.example.com/.well-known/openid-configuration | head -20
```

#### Step 3 — Test the login flow

Open `https://hecate.example.com/auth/sso/oidc/login` in a browser. You should be redirected to your IdP's sign-in page. After authenticating, you'll be redirected back to Hecate's callback, which returns:

```json
{
  "access_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "auth_method": "sso"
}
```

Use the `access_token` as a Bearer token for subsequent API calls:

```bash
curl https://hecate.example.com/api/agents \
  -H "Authorization: Bearer eyJhbGciOi..."
```

---

### Option B: SAML 2.0

Use SAML when your IdP doesn't support OIDC (typically AD FS or older enterprise IdPs).

#### Step 1 — Gather IdP metadata

From your SAML IdP, you need:

- **IdP Entity ID** — a URI uniquely identifying the IdP
- **IdP SSO URL** — where Hecate sends the AuthnRequest
- **IdP X.509 certificate** — for verifying the SAML response signature

#### Step 2 — Register Hecate as a Service Provider at your IdP

| Setting | Value |
|---------|-------|
| SP Entity ID | `https://hecate.example.com` |
| ACS (Assertion Consumer Service) URL | `https://hecate.example.com/auth/sso/saml/acs` |
| Name ID format | `urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress` |
| Signature required | Yes |

#### Step 3 — Configure Hecate

Add to `.env`:

```dotenv
# .env
SSO_SAML_SP_ENTITY_ID=https://hecate.example.com
SSO_SAML_SP_ACS_URL=https://hecate.example.com/auth/sso/saml/acs
SSO_SAML_IDP_ENTITY_ID=https://idp.example.com/saml/metadata
SSO_SAML_IDP_SSO_URL=https://idp.example.com/saml/sso
SSO_SAML_IDP_X509_CERT=MIIDXTCCAkW...
```

The X.509 cert must be the PEM-encoded cert **without** the `-----BEGIN CERTIFICATE-----` header and footer, on a single line.

#### Step 4 — Test the login flow

Open `https://hecate.example.com/auth/sso/saml/login` in a browser. The IdP's sign-in page appears. After authenticating, the IdP POSTs the SAML assertion to the ACS URL, and Hecate returns a JWT token (same JSON shape as OIDC).

---

### Option C: LDAP bind

Use LDAP when you have an existing Active Directory or OpenLDAP directory and want direct bind authentication with just-in-time (JIT) user provisioning.

#### Step 1 — Configure Hecate

Add to `.env`:

```dotenv
# .env
SSO_LDAP_SERVER_URL=ldaps://ldap.example.com:636
SSO_LDAP_BASE_DN=ou=users,dc=example,dc=com
SSO_LDAP_BIND_DN=cn=hecate-service,ou=services,dc=example,dc=com
SSO_LDAP_BIND_PASSWORD=<service-account-password>
SSO_LDAP_SEARCH_FILTER=(uid={})
SSO_LDAP_USE_SSL=true
```

| Setting | Purpose |
|---------|---------|
| `SSO_LDAP_SERVER_URL` | `ldaps://` for SSL, `ldap://` for plaintext (development only) |
| `SSO_LDAP_BASE_DN` | Where user entries live |
| `SSO_LDAP_BIND_DN` | Service account Hecate uses for the initial bind and search |
| `SSO_LDAP_BIND_PASSWORD` | Service account password |
| `SSO_LDAP_SEARCH_FILTER` | `{}` is replaced with the submitted username |
| `SSO_LDAP_USE_SSL` | `true` (default) — required for `ldaps://` |

> LDAP does **not** have an HTTP login endpoint. Users bind with their directory credentials through the main API — see [LDAP authentication API](#ldap-authentication-api) below.

#### JIT provisioning

On first successful bind, the user is created in Hecate's database with the directory's email as the username. Subsequent logins look up the existing user.

---

## Part 2 — Enable SCIM provisioning

SCIM lets your IdP automatically create, update, and delete Hecate users and groups as your directory changes. Pair it with any SSO method above.

### Step 1 — Generate a SCIM bearer token

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Add to `.env`:

```dotenv
# .env
SCIM_ENABLED=true
SCIM_BEARER_TOKEN=<paste-token-here>
```

> **Treat the SCIM bearer token as a high-privilege credential.** It can create and delete users across all workspaces. Rotate it quarterly and never commit it to source control.

Restart Hecate.

### Step 2 — Register Hecate at your IdP

Most IdPs have a "SCIM provisioning" integration. Point it at:

| Setting | Value |
|---------|-------|
| SCIM base URL | `https://hecate.example.com/scim/v2` |
| Authentication mode | Bearer token |
| Bearer token | the token from Step 1 |

### Step 3 — Test the connection

The IdP will hit these discovery endpoints:

```bash
# Verify SCIM is enabled and the token is valid
curl -H "Authorization: Bearer <token>" \
     -H "Accept: application/scim+json" \
     https://hecate.example.com/scim/v2/ServiceProviderConfig
```

Successful response (excerpt):

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
  "patch": {"supported": true},
  "filter": {"supported": true, "maxResults": 1000},
  "sort": {"supported": true},
  "etag": {"supported": true}
}
```

If you get `401 Unauthorized`, the token doesn't match `SCIM_BEARER_TOKEN`. If you get `404 Not Found`, `SCIM_ENABLED` is not set to `true`.

### Step 4 — Available SCIM endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/scim/v2/ServiceProviderConfig` | Capability discovery |
| `GET` | `/scim/v2/Schemas` | List supported attribute schemas |
| `GET` | `/scim/v2/ResourceTypes` | List available resources (User, Group) |
| `GET` | `/scim/v2/Users` | List users (filter: `?filter=userName eq "..."`) |
| `POST` | `/scim/v2/Users` | Create user |
| `GET` | `/scim/v2/Users/{id}` | Get user |
| `PUT` | `/scim/v2/Users/{id}` | Replace user |
| `PATCH` | `/scim/v2/Users/{id}` | Partial update (e.g. deactivate) |
| `DELETE` | `/scim/v2/Users/{id}` | Delete user |
| `GET` | `/scim/v2/Groups` | List groups |
| `POST` | `/scim/v2/Groups` | Create group |
| `GET` | `/scim/v2/Groups/{id}` | Get group |
| `PATCH` | `/scim/v2/Groups/{id}` | Update group membership |
| `DELETE` | `/scim/v2/Groups/{id}` | Delete group |

### Step 5 — Test user provisioning manually

```bash
# Create a user
curl -X POST https://hecate.example.com/scim/v2/Users \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
    "userName": "alice@example.com",
    "name": {
      "givenName": "Alice",
      "familyName": "Smith"
    },
    "displayName": "Alice Smith",
    "emails": [{"value": "alice@example.com", "primary": true}],
    "active": true
  }'

# List users
curl -H "Authorization: Bearer <token>" \
     -H "Accept: application/scim+json" \
     "https://hecate.example.com/scim/v2/Users?filter=userName eq \"alice@example.com\""

# Deactivate (soft-disable without deletion)
curl -X PATCH https://hecate.example.com/scim/v2/Users/<user-id> \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/scim+json" \
  -d '{
    "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
    "Operations": [{"op": "replace", "path": "active", "value": false}]
  }'
```

The deactivated user can no longer authenticate but their data is preserved.

---

## Part 3 — Production hardening

### JWT signing secret

SSO and SCIM issue Hecate JWTs. The signing key comes from `JWT_SECRET`:

```dotenv
# .env
JWT_SECRET=<strong-random-string>
```

Generate one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

> **Without `JWT_SECRET`**, SSO callbacks succeed but issued tokens may not survive a restart. Set this in any production deployment.

### CSRF state store (OIDC only)

The OIDC login flow uses an in-memory dictionary to track CSRF `state` tokens. This **does not survive restarts and does not work across multiple replicas**. In production:

1. Switch to a shared session store (Redis or PostgreSQL)
2. Or front the OIDC login with a sticky-session load balancer

### Reverse proxy headers

If Hecate is behind a reverse proxy, ensure `X-Forwarded-Proto` and `X-Forwarded-Host` are set correctly. The OIDC callback URL is derived from these headers — misconfigured proxies cause redirect loops.

```nginx
location / {
    proxy_pass http://hecate_backend;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### LDAP TLS verification

When `SSO_LDAP_USE_SSL=true`, Hecate uses the system CA bundle to verify the LDAP server certificate. If your LDAP server uses a private CA:

```bash
# In the Hecate container, install the CA cert
docker compose exec hecate \
  sh -c "echo '-----BEGIN CERTIFICATE-----...-----END CERTIFICATE-----' > /usr/local/share/ca-certificates/ldap-ca.crt && update-ca-certificates"
```

---

## LDAP authentication API

LDAP differs from OIDC/SAML: there's no browser redirect. Instead, users authenticate by sending their directory credentials to the main auth endpoint.

```bash
# Authenticate with LDAP credentials (header-based)
curl -X POST https://hecate.example.com/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "password": "<directory-password>",
    "method": "ldap"
  }'
```

> The exact endpoint shape may vary — check the Swagger UI at `/docs` for the current LDAP login route. The LDAP provider (`hecate.auth.ldap_provider.LDAPAuthProvider`) handles bind verification and JIT user creation.

---

## Troubleshooting

### OIDC login redirects but callback returns `401 Unauthorized`

The IdP rejected the token exchange. Common causes:
- **Wrong client secret** — copy it again from the IdP admin console
- **Redirect URI mismatch** — must be exactly `https://hecate.example.com/auth/sso/oidc/callback` (scheme, host, path)
- **Discovery URL unreachable** from the Hecate container — test with `docker compose exec hecate curl <discovery_url>`

### SAML response rejected with "Invalid signature"

The X.509 cert is wrong, expired, or has whitespace/newlines. The cert must be a single PEM line without `-----BEGIN/END-----` markers.

```bash
# Format a cert correctly
awk '/BEGIN CERTIFICATE/{found=1; next} /END CERTIFICATE/{found=0} found' \
  /path/to/idp.crt | tr -d '\n' > /tmp/idp-cert-oneline.txt
```

Copy the contents of `/tmp/idp-cert-oneline.txt` into `SSO_SAML_IDP_X509_CERT`.

### LDAP bind fails with `ldap.SERVER_DOWN`

Network connectivity or TLS issue. Verify:

```bash
# Test reachability from Hecate container
docker compose exec hecate \
  sh -c "echo | openssl s_client -connect ldap.example.com:636 -servername ldap.example.com 2>/dev/null | openssl x509 -noout -subject"
```

If that succeeds but Hecate still fails, check `SSO_LDAP_SERVER_URL` — `ldaps://host:port` (with explicit port).

### SCIM returns `404 Not Found`

`SCIM_ENABLED` is not `true`, or Hecate wasn't restarted after setting it. Verify:

```bash
docker compose exec hecate env | grep SCIM
```

Both `SCIM_ENABLED=true` and `SCIM_BEARER_TOKEN=...` must be present in the running container's environment.

### SCIM returns `401 Unauthorized`

The bearer token doesn't match `SCIM_BEARER_TOKEN`. Watch for whitespace — tokens copied from terminals often have trailing newlines.

### After SSO login, API calls return `401`

The JWT signing secret changed after the token was issued. Tokens signed with a different secret fail verification. Either:
- Users log in again to get a fresh token
- Or pin `JWT_SECRET` permanently in your secret manager

### JIT-provisioned LDAP user has no workspace

LDAP users are created with no workspace membership. Add them to a workspace after first login, or use SCIM to manage group → workspace mapping (if your IdP supports it).

---

## See also

- **[Environment Variables Reference](../reference/env-vars.md)** — every `SSO_*` and `SCIM_*` variable.
- **[Deploy to Production](deploy-production.md)** — reverse proxy, TLS, and secrets management for SSO callbacks.
- **[SCIM 2.0 Specification](https://www.rfc-editor.org/info/rfc7644)** — the protocol reference.
- **[OpenID Connect Discovery](https://openid.net/specs/openid-connect-discovery-1_0.html)** — for IdP-side configuration.