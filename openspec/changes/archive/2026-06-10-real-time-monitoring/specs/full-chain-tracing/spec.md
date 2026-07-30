## MODIFIED Requirements — 修改的需求

### 需求：从 API 到引擎的 OTel 上下文传播
`TracingService.end_span()` 方法应为每个完成的 span 调用 `MetricsStore.record_counter()` 和 `MetricsStore.record_histogram()`，更新请求计数、错误计数、延迟直方图和 token 使用计数器，无需在引擎或工作层进行额外插装

#### 场景：成功 span 更新请求计数器
- **当** 调用 `TracingService.end_span(record_id, status="completed")`
- **则** 应调用 `MetricsStore.record_counter("requests_total", 1, tags)` 和 `MetricsStore.record_histogram("request_latency_ms", latency_ms, tags)`

#### 场景：错误 span 更新错误计数器
- **当** 调用 `TracingService.end_span(record_id, status="error")`
- **则** 除了请求计数器外，还应调用 `MetricsStore.record_counter("errors_total", 1, tags)`

#### 场景：带使用数据的 span 更新 token 计数器
- **当** 调用 `TracingService.end_span(record_id, usage={"input_tokens": 100, "output_tokens": 50})`
- **则** 应调用 `MetricsStore.record_counter("input_tokens", 100, tags)` 和 `MetricsStore.record_counter("output_tokens", 50, tags)`

#### 场景：MetricsStore 是可选的（优雅降级）
- **当** 未配置 MetricsStore（None）
- **则** `TracingService.end_span()` 应正常完成，不尝试记录指标
