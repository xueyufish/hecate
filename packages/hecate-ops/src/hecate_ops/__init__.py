"""hecate-ops — observability pipeline domain.

OTel tracing bootstrap (otel_setup), span persistence to the core TraceModel
table plus MetricsStore feed (span_processor), the shared RuntimePort span
implementation (span_adapter), and the monitoring/metrics stack
(monitoring, metrics_storage, timescale_metrics_store, metrics, api.monitoring).

Extracted from the Hecate core package as part of the package-split plan
(PR3b). Import directly from submodules; this __init__ intentionally exports
nothing.
"""
