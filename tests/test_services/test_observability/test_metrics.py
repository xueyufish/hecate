"""Unit tests for MetricsCollector."""

from __future__ import annotations

from hecate.services.observability.metrics import MetricsCollector


class TestMetricsCollector:
    """Tests for the MetricsCollector class."""

    def test_record_request(self) -> None:
        """Test recording a request metric."""
        collector = MetricsCollector()

        collector.record_request(
            endpoint="/api/agents",
            method="GET",
            status_code=200,
            latency_ms=50.0,
        )

        assert collector.get_request_count() == 1
        assert collector.get_error_rate() == 0.0

    def test_record_error_request(self) -> None:
        """Test recording an error request."""
        collector = MetricsCollector()

        collector.record_request(
            endpoint="/api/agents",
            method="POST",
            status_code=400,
            latency_ms=10.0,
        )

        assert collector.get_request_count() == 1
        assert collector.get_error_rate() == 1.0

    def test_record_token_usage(self) -> None:
        """Test recording token usage."""
        collector = MetricsCollector()

        collector.record_token_usage(
            session_id="sess-1",
            agent_id="agent-1",
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.01,
            model="gpt-4o",
        )

        assert collector._counters["total_input_tokens"] == 100
        assert collector._counters["total_output_tokens"] == 50

    def test_increment_counter(self) -> None:
        """Test incrementing a counter."""
        collector = MetricsCollector()

        collector.increment_counter("test_counter")
        collector.increment_counter("test_counter", 5)

        assert collector._counters["test_counter"] == 6

    def test_set_gauge(self) -> None:
        """Test setting a gauge value."""
        collector = MetricsCollector()

        collector.set_gauge("active_sessions", 42.0)

        assert collector._gauges["active_sessions"] == 42.0

    def test_get_request_count_with_endpoint(self) -> None:
        """Test request count with endpoint filter."""
        collector = MetricsCollector()

        collector.record_request("/api/agents", "GET", 200, 50.0)
        collector.record_request("/api/sessions", "GET", 200, 30.0)

        assert collector.get_request_count("/api/agents") == 1
        assert collector.get_request_count("/api/sessions") == 1

    def test_get_average_latency(self) -> None:
        """Test average latency calculation."""
        collector = MetricsCollector()

        collector.record_request("/api/test", "GET", 200, 100.0)
        collector.record_request("/api/test", "GET", 200, 200.0)

        avg = collector.get_average_latency("/api/test")
        assert avg == 150.0

    def test_get_total_cost(self) -> None:
        """Test total cost calculation."""
        collector = MetricsCollector()

        collector.record_token_usage("s1", "a1", 100, 50, 0.01, "gpt-4o")
        collector.record_token_usage("s2", "a1", 200, 100, 0.02, "gpt-4o")

        total = collector.get_total_cost("a1")
        assert total == 0.03

    def test_export_prometheus(self) -> None:
        """Test Prometheus export format."""
        collector = MetricsCollector()

        collector.increment_counter("requests_total")
        collector.set_gauge("active_connections", 5.0)

        output = collector.export_prometheus()

        assert "requests_total 1" in output
        assert "active_connections 5.0" in output
        assert "# TYPE requests_total counter" in output
        assert "# TYPE active_connections gauge" in output
