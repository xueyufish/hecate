## MODIFIED Requirements

### Requirement: OTel context propagation from API to engine
The `TracingService.end_span()` method SHALL call `MetricsStore.record_counter()` and `MetricsStore.record_histogram()` for every completed span, updating request counts, error counts, latency histograms, and token usage counters without additional instrumentation in the engine or worker layers.

#### Scenario: Successful span updates request counter
- **WHEN** `TracingService.end_span(record_id, status="completed")` is called
- **THEN** `MetricsStore.record_counter("requests_total", 1, tags)` and `MetricsStore.record_histogram("request_latency_ms", latency_ms, tags)` SHALL be called

#### Scenario: Error span updates error counter
- **WHEN** `TracingService.end_span(record_id, status="error")` is called
- **THEN** `MetricsStore.record_counter("errors_total", 1, tags)` SHALL be called in addition to the request counter

#### Scenario: Span with usage data updates token counters
- **WHEN** `TracingService.end_span(record_id, usage={"input_tokens": 100, "output_tokens": 50})` is called
- **THEN** `MetricsStore.record_counter("input_tokens", 100, tags)` and `MetricsStore.record_counter("output_tokens", 50, tags)` SHALL be called

#### Scenario: MetricsStore is optional (graceful degradation)
- **WHEN** no MetricsStore is configured (None)
- **THEN** `TracingService.end_span()` SHALL complete normally without attempting to record metrics
