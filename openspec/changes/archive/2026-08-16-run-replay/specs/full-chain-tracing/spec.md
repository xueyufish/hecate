## MODIFIED Requirements

### Requirement: Trace query REST API
The system SHALL expose REST API endpoints for querying trace data. Trace queries SHALL be tenant-scoped: results SHALL only include traces belonging to the caller's tenant scope (organization/workspace), and traces outside the caller's scope SHALL never be returned or enumerated.

#### Scenario: List traces with filters
- **WHEN** `GET /api/traces?session_id=<uuid>&agent_id=<uuid>&limit=20` is called
- **THEN** a paginated list of root trace records SHALL be returned, ordered by `start_time` descending, with fields: `trace_id`, `name`, `status`, `start_time`, `end_time`, `session_id`, `agent_id`, `usage` summary

#### Scenario: Get trace detail with span tree
- **WHEN** `GET /api/traces/{trace_id}` is called
- **THEN** the trace root record SHALL be returned with all child spans in a hierarchical tree structure, including `input_data`, `output_data`, `metadata`, `usage` for each span

#### Scenario: Traces filtered by time range
- **WHEN** `GET /api/traces?start_time=2026-01-01T00:00:00Z&end_time=2026-01-02T00:00:00Z` is called
- **THEN** only traces with `start_time` within the range SHALL be returned

#### Scenario: Tenant scoping on list
- **WHEN** traces exist for multiple tenants and a caller lists traces
- **THEN** only traces within the caller's tenant scope SHALL be returned

#### Scenario: Cross-tenant detail access
- **WHEN** `GET /api/traces/{trace_id}` is called for a trace outside the caller's tenant scope
- **THEN** the system SHALL return 404
