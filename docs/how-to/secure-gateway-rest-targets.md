# Secure gateway REST targets

REST execution binds requests to the registered target origin, base path and
projected OpenAPI operation. The same executor serves ToolRegistry calls and
federated Gateway calls. Runtime validation also applies to previously saved
targets and projections.

## Manage targets

Target management (`/api/gateway/targets`) requires authentication and
checks the caller's workspace role. When `GATEWAY_ENABLED` is false, the
route dependencies return `404` before authentication.

| Action | Workspace member | Workspace ADMIN | Platform admin |
|---|---|---|---|
| List / read targets | own workspace, `VIEWER` floor | own workspace | global and own workspace; bootstrap token sees all |
| Register a `rest` target owned by the caller's workspace | — | yes | yes |
| Register or change a global target (`workspace_id: null`) | — | — | yes |
| Register or change an `mcp` target | — | — | yes |
| Deactivate a target | — | own-workspace `rest` only | yes |

- Omit `workspace_id` to register in your active workspace. An explicit
  foreign workspace ID returns `403` for a workspace-scoped caller. A
  platform bootstrap token can select any workspace and defaults to
  global scope when the field is omitted.
- Global and `mcp` mutations require a `PLATFORM_ADMIN_API_KEYS` bootstrap
  token or JWT authentication for a `PLATFORM_ADMIN_EMAILS` user.
  The MCP connection registry uses names shared across workspaces.
  A legacy `HECATE_API_KEYS` token needs an explicit platform-admin grant
  to access target management.
- A workspace API key uses its owner's current membership and role in
  the key's workspace. A removed membership loses management access.
- Unknown IDs, foreign targets and globals outside the caller's scope
  all return `404`.
- Credentials never appear in management responses: leaf values are replaced
  with a redacted marker while the key structure stays visible.
- Rejected requests write no target row, no projected tool row and no MCP
  registry entry.

## Configure a target

Use HTTPS for external targets. HTTP is supported for actual loopback IP
addresses and `localhost` during local development. A hostname beginning with
`127.` still requires HTTPS when it is a DNS name.

Configure the base URL as an origin and optional path prefix, such as
`https://crm.example.com/api/v2`. Put authentication material in the target's
brokered credential headers. Execution rejects URL user information, query
strings, fragments, malformed URLs and unsafe path components. For an existing
target using these forms, correct its configuration before retrying execution.

OpenAPI `servers` overrides are ignored. Projected methods use the converter's
supported GET, POST, PUT, PATCH and DELETE operations. Invalid methods,
unresolved placeholders and malformed parameter-routing metadata fail before
dispatch. Keep path placeholders and `path_params` consistent when maintaining
stored projections.

## Supply path data and verified identities

Scalar path parameters are encoded as segment data. Unicode names, spaces and
literal percent signs retain their values. Dot segments, separators, query or
fragment delimiters, control characters and their nested encoded forms are
rejected. Query parameters and JSON body fields follow the projection's routing
metadata. The registered base-path prefix is preserved.

Gateway identity headers come from the verified caller envelope. These reserved
names are removed from credential configuration and merged client defaults,
using case-insensitive matching.

- `x-hecate-actor`
- `x-hecate-on-behalf-of`
- `x-hecate-delegation-id`
- `x-hecate-deployment`

The HTTP Host header follows the registered URL. Brokered authentication headers
remain on that request. The executor builds request-local headers so concurrent
calls can share a client while carrying separate verified identities.

## Handle redirects and failures

Each execution explicitly disables redirect following, including when the
provided client enables it by default. An upstream 3xx raises
`RestExecutionError` with `failure_class=redirect_not_followed` and its status
code. Correct the registered target or upstream configuration before retrying.
Upstream HTTP errors use `upstream_error`; transport failures use
`connection_failure`. Diagnostics identify the target, operation and failure
class while excluding credential material.

The executor closes clients it creates on success, failure and cancellation.
The caller closes any client it supplies. Client defaults remain available for
the caller's other operations.

## Verify the profile

```bash
python scripts/dev.py check all
python scripts/dev.py test tests/test_tools/test_gateway/ \
  tests/test_core/test_credential_governance.py -q
python scripts/dev.py test tests/test_tools/test_gateway/test_rest_boundary_http.py -q -rA
```

The management authorization matrix above is asserted over the real routes by
`tests/test_tools/test_gateway/test_target_api_auth.py` with the anonymous,
workspace-role and platform-admin identity fixtures.

The HTTP tests use two real loopback receivers, synthetic credentials and
persisted OpenAPI projections. They cover both normal entry points, escaped
paths, ignored server overrides and redirect refusal. A fixed account profile
allows account A and denies account B through existing argument policy rules;
the rejected call is checked against receiver request counts. A separate direct
registry call verifies the target service's own 403 response. This profile
demonstrates the registered REST boundary and these account rules. The upstream
application remains responsible for its business authorization.
