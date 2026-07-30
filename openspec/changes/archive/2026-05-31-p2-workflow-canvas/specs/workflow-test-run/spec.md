## ADDED Requirements — 新增需求

### Requirement: Trigger workflow test run — 触发工作流测试运行
`POST /api/workflows/{id}/test-run` SHALL accept a JSON input payload, create a new PregelRuntime with the workflow's compiled graph, execute it with the provided input, and return per-node execution results.

`POST /api/workflows/{id}/test-run` 应接受 JSON 输入负载，使用工作流的编译图创建新的 PregelRuntime，使用提供的输入执行它，并返回每个节点的执行结果。

#### Scenario: Successful test run — 成功的测试运行
- **WHEN** POST /api/workflows/{id}/test-run with `{"input": {"messages": [{"role": "user", "content": "Hello"}]}}`
- **THEN** response is 200 with `{"run_id": "...", "status": "completed", "nodes": [{"node_id": "...", "status": "completed", "output": {...}, "duration_ms": 123}]}`
- **当** POST /api/workflows/{id}/test-run，参数为 `{"input": {"messages": [{"role": "user", "content": "Hello"}]}}`
- **则**响应为 200，包含 `{"run_id": "...", "status": "completed", "nodes": [{"node_id": "...", "status": "completed", "output": {...}, "duration_ms": 123}]}`

#### Scenario: Test run with invalid workflow — 测试运行无效工作流
- **WHEN** POST /api/workflows/{id}/test-run for a workflow with no compiled version
- **THEN** response is 400 with error "Workflow has no compiled version"
- **当** POST /api/workflows/{id}/test-run，目标为没有编译版本的工作流
- **则**响应为 400，错误信息为"工作流没有编译版本"

### Requirement: Mock mode for test runs — 测试运行的模拟模式
The system SHALL support a `mock: true` query parameter on test-run that replaces all LLM calls with canned responses, allowing testing without API key consumption.

系统应支持 test-run 上的 `mock: true` 查询参数，将所有 LLM 调用替换为预设响应，从而在无需消耗 API key 的情况下进行测试。

#### Scenario: Test run in mock mode — 在模拟模式下测试运行
- **WHEN** POST /api/workflows/{id}/test-run?mock=true
- **THEN** all conversation nodes return a fixed mock response "Mock response from {model}" and no real LLM API calls are made
- **当** POST /api/workflows/{id}/test-run?mock=true
- **则**所有 conversation 节点返回固定的模拟响应"来自 {model} 的模拟响应"，且不进行真实的 LLM API 调用

### Requirement: Per-node execution status — 每个节点的执行状态
The test run response SHALL include execution status for each node: `pending`, `running`, `completed`, `error`, or `skipped`.

测试运行响应应包含每个节点的执行状态：`pending`（待处理）、`running`（运行中）、`completed`（已完成）、`error`（错误）或 `skipped`（已跳过）。

#### Scenario: Condition node skips a branch — 条件节点跳过某个分支
- **WHEN** a condition node evaluates to "true" and the "false" branch nodes exist
- **THEN** "true" branch nodes have status "completed" and "false" branch nodes have status "skipped"
- **当**条件节点评估为"true"且"false"分支节点存在
- **则**"true"分支节点状态为"completed"，"false"分支节点状态为"skipped"

#### Scenario: Node execution error — 节点执行错误
- **WHEN** a conversation node fails during test run (e.g., invalid model)
- **THEN** that node has status "error" with error_message, and downstream nodes have status "skipped"
- **当** conversation 节点在测试运行期间失败（例如，无效模型）
- **则**该节点状态为"error"并带有 error_message，下游节点状态为"skipped"

### Requirement: Test run duration tracking — 测试运行耗时跟踪
Each node's execution result SHALL include `duration_ms` measuring wall-clock time from node start to completion.

每个节点的执行结果应包含 `duration_ms`，测量从节点开始到完成的实际耗时。

#### Scenario: Duration recorded — 记录耗时
- **WHEN** a test run completes
- **THEN** each completed node entry includes a positive `duration_ms` value
- **当**测试运行完成
- **则**每个已完成的节点条目包含一个正的 `duration_ms` 值

### Requirement: List workflow test runs — 列出工作流测试运行
`GET /api/workflows/{id}/runs` SHALL return a paginated list of past test runs with run_id, status, created_at, and duration.

`GET /api/workflows/{id}/runs` 应返回过去测试运行的分页列表，包含 run_id、status、created_at 和 duration。

#### Scenario: List recent runs — 列出最近的运行
- **WHEN** GET /api/workflows/{id}/runs?page=1&page_size=10
- **THEN** response is 200 with `{"items": [{"run_id": "...", "status": "completed", "created_at": "...", "duration_ms": 1234}], "total": int}`
- **当** GET /api/workflows/{id}/runs?page=1&page_size=10
- **则**响应为 200，包含 `{"items": [{"run_id": "...", "status": "completed", "created_at": "...", "duration_ms": 1234}], "total": int}`
