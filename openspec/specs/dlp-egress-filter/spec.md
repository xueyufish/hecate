# dlp-egress-filter Specification

## Purpose
TBD - created by archiving change outbound-dlp-engine. Update Purpose after archive.
## Requirements
### Requirement: EgressFilter abstract base class
The system SHALL define `EgressFilter` ABC in `services/security/egress.py` with class attribute `name` (str) and abstract async method `filter(content: Any, direction: str, context: dict) -> EgressResult`.

#### Scenario: Cannot instantiate directly
- **WHEN** `EgressFilter()` is called
- **THEN** `TypeError` SHALL be raised

#### Scenario: Subclass with implementation succeeds
- **WHEN** a class inherits from `EgressFilter` and implements `filter`
- **THEN** the class CAN be instantiated

### Requirement: EgressAction enum
The system SHALL define `EgressAction` as a `StrEnum` with three members: `ALLOW`, `BLOCK`, `MODIFIED`.

#### Scenario: Three members
- **WHEN** `len(EgressAction)` is evaluated
- **THEN** the result is `3`

### Requirement: EgressResult dataclass
The system SHALL define `EgressResult` dataclass with fields: `action` (EgressAction), `content` (Any), `reason` (str), `audit_data` (list[dict]).

#### Scenario: Allow result
- **WHEN** `EgressResult(action=EgressAction.ALLOW, content=<original>)` is constructed
- **THEN** all fields SHALL be accessible

### Requirement: DLPEgressFilter implementation
The system SHALL define `DLPEgressFilter(EgressFilter)` that wraps a DLPScanner and applies DLP scanning to content based on direction.

#### Scenario: Text content scanned
- **WHEN** `filter(content="email is john@example.com", direction="mcp_response", context={"tool_name": "search"})`
- **THEN** it SHALL call DLPScanner.scan() and return the result mapped to EgressResult

#### Scenario: BLOCK result
- **WHEN** scan result action is BLOCK
- **THEN** DLPEgressFilter SHALL return `EgressResult(action=BLOCK, content="[Response blocked by DLP policy]", reason=...)`

#### Scenario: MASK result
- **WHEN** scan result action is MASK
- **THEN** DLPEgressFilter SHALL return `EgressResult(action=MODIFIED, content=<masked_text>)`

#### Scenario: Non-text passthrough
- **WHEN** content is bytes or image (not str)
- **THEN** DLPEgressFilter SHALL return `EgressResult(action=ALLOW, content=<original>, audit_data=[non-text-passthrough entry])`

### Requirement: HecateMCPClient accepts egress filters
The `HecateMCPClient` SHALL accept an optional `egress_filters: list[EgressFilter]` constructor parameter.

#### Scenario: No filters (backward compatible)
- **WHEN** HecateMCPClient is constructed without `egress_filters`
- **THEN** behavior SHALL be identical to current (no egress filtering)

#### Scenario: Filters applied to MCP responses
- **WHEN** HecateMCPClient is constructed with `egress_filters=[dlp_filter]`
- **THEN** `call_tool()` SHALL pass the result through the filter chain before returning

#### Scenario: BLOCK stops the chain
- **WHEN** first filter returns BLOCK
- **THEN** `call_tool()` SHALL stop processing and return the block message

### Requirement: MCP response direction scanning
The system SHALL call DLPScanner.scan() with `direction="mcp_response"` for any MCP tool response.

#### Scenario: MCP response scanned
- **WHEN** `call_tool("search", {"q": "test"})` returns `"Found: john@example.com"`
- **THEN** DLPScanner.scan() SHALL be called with text="Found: john@example.com", direction="mcp_response", context={"tool_name": "search", "server_url": ...}

#### Scenario: BLOCK replaces response
- **WHEN** MCP response contains AWS_ACCESS_KEY and policy says BLOCK
- **THEN** `call_tool()` SHALL return the block message instead of the actual response

#### Scenario: MASK modifies response
- **WHEN** MCP response contains EMAIL and policy says MASK
- **THEN** `call_tool()` SHALL return the masked text (e.g., "Found: [EMAIL]")

#### Scenario: DLP disabled
- **WHEN** `DLP_ENABLED=False` or `egress_filters` is empty
- **THEN** MCP responses SHALL pass through unchanged

