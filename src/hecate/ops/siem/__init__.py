"""SIEM export pipeline — unified security event export to external SIEM systems.

Provides:
- :class:`SecurityEvent` — normalized event from AuditLog, ToolDecision, SecurityFinding
- :class:`SIEMExporter` — ABC for export sinks
- :class:`SecurityEventCollector` — subscribes to all sources, normalizes, filters, routes
- :class:`WebhookSIEMExporter` — HTTPS POST export (Splunk HEC, Datadog, Elastic, generic)
- :class:`SyslogSIEMExporter` — RFC 5424 syslog export over TCP/UDP + TLS
- :class:`OCSFFormatter` — OCSF v1.5 schema mapping
"""
