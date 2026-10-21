# Spec Delta

## ADDED Requirements

### Requirement: Aggregate report access boundary

The workspace-aggregate report endpoints (`GET /api/evaluation/reports/overview`, `GET /api/evaluation/reports/trends`, `GET /api/evaluation/reports/breakdowns`) SHALL enforce one shared access boundary before any service call: a caller with an active workspace SHALL hold a minimum `viewer` role, and a membership identity whose role is below viewer (or absent) SHALL receive 403 `FORBIDDEN`. A caller without an active workspace — including explicit system-scope identities, because these endpoints take no target-resource or workspace-selection parameter — SHALL receive 403 `NO_WORKSPACE`, and the boundary SHALL NOT add a global aggregation or workspace-selection capability for them. The service SHALL only ever receive the verified active workspace identifier; no zero-UUID or empty-workspace fallback SHALL be introduced. A valid workspace with no data SHALL keep returning a normal empty report. The boundary SHALL NOT modify any data and SHALL NOT change successful response shapes, time-window filters, bucketing, metric filtering, error-score handling, the overview/trends human-override reconciliation, the breakdowns raw per-source statistics, or the distributions and sessions endpoints' own boundaries.

#### Scenario: Viewer, editor, and admin read their workspace aggregates
- **WHEN** an identity holding viewer, editor, or admin role in its active workspace calls overview, trends, or breakdowns
- **THEN** each endpoint SHALL return 200 with its existing response shape for that workspace's data

#### Scenario: Role-less member of a workspace is denied
- **WHEN** a membership identity without a role calls any of the three endpoints
- **THEN** the response SHALL be 403 `FORBIDDEN` and no aggregation SHALL run

#### Scenario: Missing workspace is 403 NO_WORKSPACE including system
- **WHEN** a validly authenticated identity without an active workspace — including an explicit system-scope identity — calls any of the three endpoints
- **THEN** the response SHALL be 403 with error code `NO_WORKSPACE`, no aggregation SHALL run, and no global-aggregation or workspace-selection parameter SHALL be introduced

#### Scenario: Empty workspace keeps normal empty reports
- **WHEN** a viewer-or-higher caller's verified workspace contains no evaluation data
- **THEN** each endpoint SHALL return its existing empty-report response (zeroed overview, empty series/breakdowns) rather than an error

#### Scenario: Boundary preserves aggregation semantics and read-only guarantee
- **WHEN** requests succeed or are rejected by the boundary
- **THEN** no data SHALL be created, modified, or deleted; time-window filters, bucketing, metric filtering, and error-score handling remain unchanged; overview and trends keep the human-override reconciliation, breakdowns keeps raw per-source rows, and the distributions and sessions endpoints keep their own boundaries
