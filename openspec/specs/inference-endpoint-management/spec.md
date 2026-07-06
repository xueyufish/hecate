# inference-endpoint-management Specification

## Purpose
TBD - created by archiving change model-hub-completion. Update Purpose after archive.
## Requirements
### Requirement: System registers external inference endpoints
The system SHALL allow administrators to register external inference endpoints with URL, model_id, backend_type (vllm/ollama/openai-compatible/custom), and optional authentication credentials.

#### Scenario: Register vLLM endpoint
- **WHEN** an administrator registers an endpoint `{url: "http://gpu-node:8000", model_id: "llama-3-70b", backend_type: "vllm"}`
- **THEN** the system stores the endpoint and begins periodic health checks

#### Scenario: Register Ollama endpoint
- **WHEN** an administrator registers an endpoint `{url: "http://localhost:11434", model_id: "llama3", backend_type: "ollama"}`
- **THEN** the system stores the endpoint with Ollama-specific health check configuration

### Requirement: System polls endpoint health periodically
The system SHALL poll each registered endpoint's `/health` endpoint at a configurable interval (default 30 seconds) and record health status (healthy/degraded/unreachable).

#### Scenario: Healthy endpoint
- **WHEN** the health check receives HTTP 200 from `/health` within the timeout
- **THEN** the endpoint status SHALL be `healthy`

#### Scenario: Degraded endpoint
- **WHEN** the health check receives a response but `/v1/models` does not list the expected model
- **THEN** the endpoint status SHALL be `degraded` with a message indicating model not loaded

#### Scenario: Unreachable endpoint
- **WHEN** the health check times out after 3 retry attempts
- **THEN** the endpoint status SHALL be `unreachable` and an alert event SHALL be emitted

### Requirement: System collects inference metrics from endpoints
The system SHALL collect Prometheus-compatible metrics from endpoints that expose them (TTFT, throughput, error rate, KV cache hit rate) and store time-series data for monitoring.

#### Scenario: Collect vLLM metrics
- **WHEN** a vLLM endpoint exposes `/metrics` in Prometheus format
- **THEN** the system SHALL scrape and store TTFT, time-between-tokens, requests-per-second, and GPU memory utilization

#### Scenario: Endpoint without metrics
- **WHEN** an endpoint does not expose Prometheus metrics (e.g., commercial API)
- **THEN** the system SHALL skip metrics collection and rely on TraceModel data for performance monitoring

### Requirement: System routes requests to healthy endpoints
The system SHALL route model invocation requests to healthy endpoints only, avoiding degraded or unreachable endpoints.

#### Scenario: Route to healthy endpoint
- **WHEN** a model has 2 registered endpoints, one `healthy` and one `unreachable`
- **THEN** requests SHALL route to the healthy endpoint only

#### Scenario: All endpoints unreachable
- **WHEN** all endpoints for a model are `unreachable`
- **THEN** the system SHALL fall back to alternative providers or return a clear error indicating no available inference endpoint

### Requirement: InferenceBackendABC defines endpoint interaction interface
The system SHALL define `InferenceBackendABC` with `health_check(endpoint)` and `invoke(endpoint, request)` abstract methods, with a `OpenAICompatibleBackend` builtin implementation.

#### Scenario: OpenAI-compatible backend
- **WHEN** an endpoint has `backend_type: "vllm"` or `"ollama"` or `"openai-compatible"`
- **THEN** the `OpenAICompatibleBackend` SHALL handle health checks via `/health` and invocations via `/v1/chat/completions`

