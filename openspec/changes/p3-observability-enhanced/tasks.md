## 1. Enhanced Tracing

- [x] 1.1 Enhance LangFuse integration — Trace→Span→Generation hierarchy
- [x] 1.2 Implement cost attribution — per user, agent, session
- [x] 1.3 Associate EvidenceTracker with LangFuse traces

## 2. Structured Logging

- [x] 2.1 Create `services/observability/structured_logger.py` with StructuredLogger
- [x] 2.2 Implement JSON log format — timestamp, level, message, context
- [x] 2.3 Implement context enrichment — session_id, agent_id, user_id

## 3. Prometheus Metrics

- [x] 3.1 Create `services/observability/metrics.py` with MetricsCollector
- [x] 3.2 Implement request metrics — count, latency, error rate
- [x] 3.3 Implement token metrics — input_tokens, output_tokens, cost
- [x] 3.4 Expose /metrics endpoint in FastAPI app

## 4. Testing

- [x] 4.1 Unit tests for StructuredLogger — JSON format, context enrichment
- [x] 4.2 Unit tests for MetricsCollector — request metrics, token metrics
- [x] 4.3 Integration test for /metrics endpoint
