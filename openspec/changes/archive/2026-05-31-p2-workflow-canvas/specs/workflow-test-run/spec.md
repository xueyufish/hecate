## ADDED Requirements

### Requirement: Trigger workflow test run
`POST /api/workflows/{id}/test-run` SHALL accept a JSON input payload, create a new PregelRuntime with the workflow's compiled graph, execute it with the provided input, and return per-node execution results.

#### Scenario: Successful test run
- **WHEN** POST /api/workflows/{id}/test-run with `{"input": {"messages": [{"role": "user", "content": "Hello"}]}}`
- **THEN** response is 200 with `{"run_id": "...", "status": "completed", "nodes": [{"node_id": "...", "status": "completed", "output": {...}, "duration_ms": 123}]}`

#### Scenario: Test run with invalid workflow
- **WHEN** POST /api/workflows/{id}/test-run for a workflow with no compiled version
- **THEN** response is 400 with error "Workflow has no compiled version"

### Requirement: Mock mode for test runs
The system SHALL support a `mock: true` query parameter on test-run that replaces all LLM calls with canned responses, allowing testing without API key consumption.

#### Scenario: Test run in mock mode
- **WHEN** POST /api/workflows/{id}/test-run?mock=true
- **THEN** all conversation nodes return a fixed mock response "Mock response from {model}" and no real LLM API calls are made

### Requirement: Per-node execution status
The test run response SHALL include execution status for each node: `pending`, `running`, `completed`, `error`, or `skipped`.

#### Scenario: Condition node skips a branch
- **WHEN** a condition node evaluates to "true" and the "false" branch nodes exist
- **THEN** "true" branch nodes have status "completed" and "false" branch nodes have status "skipped"

#### Scenario: Node execution error
- **WHEN** a conversation node fails during test run (e.g., invalid model)
- **THEN** that node has status "error" with error_message, and downstream nodes have status "skipped"

### Requirement: Test run duration tracking
Each node's execution result SHALL include `duration_ms` measuring wall-clock time from node start to completion.

#### Scenario: Duration recorded
- **WHEN** a test run completes
- **THEN** each completed node entry includes a positive `duration_ms` value

### Requirement: List workflow test runs
`GET /api/workflows/{id}/runs` SHALL return a paginated list of past test runs with run_id, status, created_at, and duration.

#### Scenario: List recent runs
- **WHEN** GET /api/workflows/{id}/runs?page=1&page_size=10
- **THEN** response is 200 with `{"items": [{"run_id": "...", "status": "completed", "created_at": "...", "duration_ms": 1234}], "total": int}`
