## 1. Frontend: Custom Input Form

- [x] 1.1 Add input form panel in workflow editor with fields: messages array (textarea for JSON), custom variables (key-value pairs)
- [x] 1.2 Add "Input" button that toggles the input form visibility
- [x] 1.3 Store input data in state, pass to test-run API as `input_data`
- [x] 1.4 Add validation: messages must be valid JSON array

## 2. Frontend: Node Output Panel

- [x] 2.1 Add node click handler that opens a side panel showing node details
- [x] 2.2 Display node_id, node_type, status, input, output, error_message, duration_ms
- [x] 2.3 Truncate output to 1000 chars with "Show more" expand button
- [x] 2.4 Show error_message prominently for failed nodes

## 3. Frontend: Execution Logs Panel

- [x] 3.1 Add logs panel below the canvas (collapsible)
- [x] 3.2 Display per-node execution order, start time, end time, status
- [x] 3.3 Format as timestamped log entries

## 4. Frontend: Node Status Badges

- [x] 4.1 Add status badge to each node component (pending/running/completed/failed)
- [x] 4.2 Color coding: gray=pending, yellow=running, green=completed, red=failed
- [x] 4.3 Update node data with test result status after run completes
- [x] 4.4 Clear badges when result panel is closed

## 5. Frontend: Run History

- [x] 5.1 Add runHistory state (array of TestRunData, max 10)
- [x] 5.2 Push each test result to history after run completes
- [x] 5.3 Add "History" button that shows dropdown with previous runs (timestamp, status, duration)
- [x] 5.4 Click history entry to load that run's results

## 6. Verification

- [x] 6.1 Run `npm run lint` in `web/` — zero errors (1 pre-existing warning)
- [x] 6.2 Run `npm run build` in `web/` — zero errors
