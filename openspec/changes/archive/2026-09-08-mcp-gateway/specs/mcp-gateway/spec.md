## Purpose

MCP Gateway federates tools from multiple sources — converted OpenAPI/REST operations and external MCP servers — into the single `/mcp` endpoint, with workspace-scoped authorization at the MCP protocol boundary and server-side credential brokering, so external MCP clients see one unified, identity-filtered tool catalog.

## ADDED Requirements

### Requirement: REST target registration projects OpenAPI operations as tools
The system SHALL allow registering a gateway target of kind `rest` consisting of a name, a base URL, an optional OpenAPI 3.x document, optional static credentials, and an owning workspace. When an OpenAPI document is provided, the system SHALL project each supported operation (path × method) as a tool row with `source="rest"`, named `<target>__<operationId or path-derived name>`, carrying the operation's parameter and request-body schemas as the tool's JSON Schema. Operations using unsupported constructs (callbacks, links) SHALL be skipped with import warnings; the import SHALL NOT fail partially without a report.

#### Scenario: Import a valid OpenAPI spec
- **WHEN** a workspace admin registers a `rest` target with a valid OpenAPI document containing three operations
- **THEN** three tool rows with `source="rest"` are created in that workspace, named `<target>__<operation>`, each carrying its operation schema as JSON Schema, and the import report lists three imported operations

#### Scenario: Invalid OpenAPI document rejected
- **WHEN** a `rest` target is registered with a malformed OpenAPI document
- **THEN** the registration is rejected with a structured validation error and no tool rows are created

### Requirement: REST tool execution is spec-driven HTTP
The system SHALL execute a `source="rest"` tool by translating the tool call arguments into an HTTP request against the target's base URL according to the operation's specification (method, path parameters, query parameters, request body), without deployed wrapper code. The request SHALL use the target's stored credentials. Non-2xx responses and connection failures SHALL surface as structured errors identifying the target and operation.

#### Scenario: Tool call translated to HTTP request
- **WHEN** a caller invokes the rest tool `crm__get_contact` with `{"contact_id": "42"}` and the operation specifies `GET /contacts/{contact_id}`
- **THEN** the gateway issues `GET <base-url>/contacts/42` with the target's credentials attached and returns the response payload as the tool result

#### Scenario: Backend failure surfaces as structured error
- **WHEN** the target backend returns HTTP 500 or is unreachable
- **THEN** the tool call returns a structured error naming the target, the operation, and the failure class (upstream error / connection failure), and does not retry automatically

### Requirement: MCP target federation exposes external server tools
The system SHALL allow registering a gateway target of kind `mcp` pointing at an external MCP server, reusing the existing MCP connection management (pool, health checks, circuit breaker). Tools discovered from that server SHALL be exposed through the gateway under presentation names `<target>__<mcp_tool_name>`, listed via `tools/list`, and executed by forwarding through the connection manager. Two targets exposing identically-named tools SHALL both be listable without collision.

#### Scenario: Federated listing and invocation
- **WHEN** an `mcp` target `github` is registered and its upstream server exposes a tool `create_issue`
- **THEN** the gateway lists `github__create_issue` and a `tools/call` on that name forwards to the upstream tool `create_issue` and returns its result

#### Scenario: Cross-target name collision is impossible
- **WHEN** two `mcp` targets `github` and `gitlab` each expose an upstream tool named `create_issue`
- **THEN** `tools/list` returns both `github__create_issue` and `gitlab__create_issue` as distinct entries

### Requirement: Gateway target credentials are brokered server-side
The system SHALL store static target credentials (e.g. header values, API keys) server-side, attach them to outbound calls, and SHALL NOT return credential values in any management API response — responses SHALL carry a redacted marker only. The v1 credential model is static configuration only; OAuth flows and credential rotation are out of scope.

#### Scenario: Credential redacted in API responses
- **WHEN** a target is created with a credential and later read through the management API
- **THEN** the response contains a redacted placeholder (e.g. `***`) in place of the credential value

#### Scenario: Credential attached to outbound call only
- **WHEN** the gateway executes a federated or rest tool for a target holding credentials
- **THEN** the credential is attached to the outbound request but never appears in tool results, error messages, or logs

### Requirement: Gateway target egress baseline
The system SHALL reject target registration with a non-HTTPS URL on a non-loopback host. Loopback HTTP endpoints SHALL be permitted for development use.

#### Scenario: Insecure non-loopback target rejected
- **WHEN** a target is registered with `http://api.example.com`
- **THEN** the registration is rejected with an error stating HTTPS is required for non-loopback hosts

### Requirement: Caller-scoped tool catalog at the gateway boundary
The system SHALL resolve the caller's workspace identity from the authenticated workspace-scoped API key and SHALL return from `tools/list` only the first-party tools plus federated tools owned by that workspace. Federated tools owned by other workspaces SHALL be hidden from both `tools/list` and `tools/call`. Platform-scope (legacy global) keys SHALL see the first-party catalog only.

#### Scenario: Workspace key sees own federation only
- **WHEN** a caller authenticates with a workspace-scoped key for workspace A and calls `tools/list`
- **THEN** the response contains first-party tools plus workspace A's federated tools, and no tools from workspace B's targets

#### Scenario: Hidden tool is not callable
- **WHEN** a caller for workspace A calls `tools/call` on a federated tool owned by workspace B
- **THEN** the gateway denies the call with an authorization error and the backend target is not contacted

### Requirement: Policy pipeline enforcement on gateway calls
The system SHALL evaluate gateway `tools/call` requests through the tool policy pipeline with the caller's identity context; a DENY decision SHALL short-circuit the call before any backend or builtin execution. Visibility-level (HIDE) decisions SHALL exclude the tool from the caller's `tools/list` result.

#### Scenario: Deny-policy tool rejected at the gateway
- **WHEN** a tool visible to the caller carries a policy that evaluates to DENY for the caller's context and the caller invokes it via `tools/call`
- **THEN** the gateway denies the call with a policy-violation error, the decision is recorded, and no execution occurs

### Requirement: Gateway calls carry attribution into tool analytics
The system SHALL record gateway-forwarded tool calls (rest and mcp) into the existing tool execution analytics with the caller's workspace identity, the target name, and the resolved tool name.

#### Scenario: Gateway call appears in analytics with workspace attribution
- **WHEN** a workspace-scoped caller invokes a federated tool through the gateway
- **THEN** the execution analytics contain a record for that call carrying the caller workspace, target, and tool names

### Requirement: Gateway feature switch
The system SHALL provide a `GATEWAY_ENABLED` setting, default `False`. When disabled, the gateway contributes no federated tools, `tools/list` returns exactly the first-party catalog, and gateway management APIs are unavailable.

#### Scenario: Gateway disabled by default
- **WHEN** no gateway-related env vars are set
- **THEN** `GATEWAY_ENABLED=False`, `/mcp` exposes only first-party tools, and the management API for targets returns 404-class responses

#### Scenario: Gateway enabled
- **WHEN** `GATEWAY_ENABLED=true`
- **THEN** registered targets' tools appear in caller-scoped `tools/list` responses and gateway management APIs are available
