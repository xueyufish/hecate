## ADDED Requirements

### Requirement: Custom Input Form
The workflow editor SHALL provide an input form for test runs. Users SHALL be able to specify input data (messages array and/or custom variables) before clicking Run Test.

#### Scenario: Custom input
- **WHEN** the user opens the input form and enters custom messages
- **THEN** the test run SHALL use the custom messages instead of the default "test" message

#### Scenario: Default input
- **WHEN** the user runs a test without modifying the input form
- **THEN** the test run SHALL use the default input: `{messages: [{role: "user", content: "test"}]}`

### Requirement: Node Output Panel
After a test run, clicking a node on the canvas SHALL open a side panel showing that node's input data, output data, error message (if any), and execution duration.

#### Scenario: View node output
- **WHEN** the user clicks a completed node after a test run
- **THEN** the system SHALL display a panel with: node_id, input, output (truncated to 1000 chars with expand), error, duration_ms

#### Scenario: View failed node
- **WHEN** the user clicks a failed node after a test run
- **THEN** the system SHALL display the error_message prominently with the node's input data

### Requirement: Execution Logs Panel
The workflow editor SHALL display an execution logs panel after a test run. The panel SHALL show per-node logs with timestamps and execution order.

#### Scenario: View execution logs
- **WHEN** a test run completes
- **THEN** the system SHALL display logs showing each node's execution order, start time, end time, and status

### Requirement: Node Status Badges
During and after a test run, each node on the canvas SHALL display a status badge indicating its state: pending (gray), running (yellow), completed (green), failed (red).

#### Scenario: Node status after run
- **WHEN** a test run completes
- **THEN** each node SHALL display a colored badge: green for completed, red for failed

#### Scenario: Clear status
- **WHEN** the user closes the test result panel
- **THEN** all node status badges SHALL be removed

### Requirement: Run History
The workflow editor SHALL maintain a list of the last 10 test runs in memory. Users SHALL be able to view previous run results and compare outputs.

#### Scenario: View run history
- **WHEN** the user clicks "History" button
- **THEN** the system SHALL display a list of previous test runs with timestamp, status, and duration

#### Scenario: Load previous run
- **WHEN** the user clicks a previous run in the history
- **THEN** the system SHALL display that run's results (node outputs, logs)
