## ADDED Requirements — 新增需求

### Requirement: 系统注册外部推理端点 — System registers external inference endpoints
系统应允许管理员注册外部推理端点，包含 URL、model_id、backend_type（vllm/ollama/openai-compatible/custom）和可选的认证凭据。

#### Scenario: 注册 vLLM 端点 — Register vLLM endpoint
- **WHEN** 管理员注册端点 `{url: "http://gpu-node:8000", model_id: "llama-3-70b", backend_type: "vllm"}`
- **THEN** 系统存储该端点并开始定期健康检查

#### Scenario: 注册 Ollama 端点 — Register Ollama endpoint
- **WHEN** 管理员注册端点 `{url: "http://localhost:11434", model_id: "llama3", backend_type: "ollama"}`
- **THEN** 系统存储该端点，并附带 Ollama 特定的健康检查配置

### Requirement: 系统定期轮询端点健康状态 — System polls endpoint health periodically
系统应以可配置的间隔（默认 30 秒）轮询每个已注册端点的 `/health` 端点，并记录健康状态（healthy/degraded/unreachable）。

#### Scenario: 健康端点 — Healthy endpoint
- **WHEN** 健康检查在超时时间内从 `/health` 收到 HTTP 200
- **THEN** 端点状态应为 `healthy`

#### Scenario: 降级端点 — Degraded endpoint
- **WHEN** 健康检查收到响应，但 `/v1/models` 未列出预期的模型
- **THEN** 端点状态应为 `degraded`，附带指示模型未加载的消息

#### Scenario: 不可达端点 — Unreachable endpoint
- **WHEN** 健康检查在 3 次重试后超时
- **THEN** 端点状态应为 `unreachable`，并应发出告警事件

### Requirement: 系统从端点收集推理指标 — System collects inference metrics from endpoints
系统应从暴露 Prometheus 兼容指标的端点收集数据（TTFT、吞吐量、错误率、KV 缓存命中率），并存储时间序列数据用于监控。

#### Scenario: 收集 vLLM 指标 — Collect vLLM metrics
- **WHEN** vLLM 端点以 Prometheus 格式暴露 `/metrics`
- **THEN** 系统应抓取并存储 TTFT、token 间时间、每秒请求数和 GPU 内存利用率

#### Scenario: 无指标的端点 — Endpoint without metrics
- **WHEN** 端点未暴露 Prometheus 指标（例如商业 API）
- **THEN** 系统应跳过指标收集，并依赖 TraceModel 数据进行性能监控

### Requirement: 系统将请求路由到健康端点 — System routes requests to healthy endpoints
系统应仅将模型调用请求路由到健康端点，避免使用降级或不可达的端点。

#### Scenario: 路由到健康端点 — Route to healthy endpoint
- **WHEN** 一个模型有 2 个已注册端点，一个 `healthy` 和一个 `unreachable`
- **THEN** 请求应仅路由到健康端点

#### Scenario: 所有端点不可达 — All endpoints unreachable
- **WHEN** 模型的所有端点都不可达
- **THEN** 系统应回退到替代提供商，或返回清晰错误，指示没有可用的推理端点

### Requirement: InferenceBackendABC 定义端点交互接口 — InferenceBackendABC defines endpoint interaction interface
系统应定义 `InferenceBackendABC`，包含 `health_check(endpoint)` 和 `invoke(endpoint, request)` 抽象方法，并附带内置的 `OpenAICompatibleBackend` 实现。

#### Scenario: OpenAI 兼容后端 — OpenAI-compatible backend
- **WHEN** 端点的 `backend_type: "vllm"` 或 `"ollama"` 或 `"openai-compatible"`
- **THEN** `OpenAICompatibleBackend` 应通过 `/health` 处理健康检查，通过 `/v1/chat/completions` 处理调用
