## 1. Enhanced Tracing

- [ ] 1.1 Enhance LangFuse integration — Trace→Span→Generation hierarchy
- [ ] 1.2 Implement cost attribution — per user, agent, session
- [ ] 1.3 Associate EvidenceTracker with LangFuse traces

## 2. Structured Logging

- [ ] 2.1 Create `services/observability/structured_logger.py` with StructuredLogger
- [ ] 2.2 Implement JSON log format — timestamp, level, message, context
- [ ] 2.3 Implement context enrichment — session_id, agent_id, user_id

## 3. Prometheus Metrics

- [ ] 3.1 Create `services/observability/metrics.py` with MetricsCollector
- [ ] 3.2 Implement request metrics — count, latency, error rate
- [ ] 3.3 Implement token metrics — input_tokens, output_tokens, cost
- [ ] 3.4 Expose /metrics endpoint in FastAPI app

## 4. Testing

- [ ] 4.1 Unit tests for StructuredLogger — JSON format, context enrichment
- [ ] 4.2 Unit tests for MetricsCollector — request metrics, token metrics
- [ ] 4.3 Integration test for /metrics endpoint
