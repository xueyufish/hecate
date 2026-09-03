"""OCSFFormatter — OCSF v1.5 schema mapping for security events.

Transforms SecurityEvent into OCSF-compliant JSON. Designed as a
decorator that wraps another SIEMExporter (typically webhook).

OCSF class mappings:
- API events → Activity (class 4001)
- Tool decision events → Authorization (class 2201)
- Anomaly events → Security Finding (class 2001)
"""

from __future__ import annotations

import logging
from typing import Any

from hecate.ops.siem.event import EventSeverity, EventType, SecurityEvent
from hecate.ops.siem.exporter import SIEMExporter

logger = logging.getLogger(__name__)

# OCSF severity_id mapping (0=Unknown, 1=Informational, 2=Low, 3=Medium, 4=High, 5=Critical, 99=Other)
_SEVERITY_TO_OCSF = {
    EventSeverity.INFO: 1,
    EventSeverity.LOW: 2,
    EventSeverity.MEDIUM: 3,
    EventSeverity.HIGH: 4,
    EventSeverity.CRITICAL: 5,
}


class OCSFFormatter(SIEMExporter):
    """Decorator that wraps another exporter with OCSF v1.5 schema mapping.

    Args:
        wrapped: The underlying exporter that receives transformed events.
    """

    def __init__(self, wrapped: SIEMExporter) -> None:
        self._wrapped = wrapped

    @property
    def name(self) -> str:
        return f"ocsf+{self._wrapped.name}"

    def _transform(self, event: SecurityEvent) -> dict[str, Any]:
        """Transform a SecurityEvent into an OCSF-compliant dictionary."""
        severity_id = _SEVERITY_TO_OCSF.get(event.severity, 1)
        timestamp_ms = int(event.timestamp.timestamp() * 1000)

        if event.event_type == EventType.API:
            return self._map_activity(event, severity_id, timestamp_ms)
        if event.event_type == EventType.TOOL_POLICY:
            return self._map_authorization(event, severity_id, timestamp_ms)
        return self._map_finding(event, severity_id, timestamp_ms)

    def _map_activity(self, event: SecurityEvent, severity_id: int, timestamp_ms: int) -> dict[str, Any]:
        """Map API event to OCSF Activity class (4001)."""
        return {
            "class_uid": 4001,
            "class_name": "Activity Audit",
            "activity_name": event.action,
            "severity_id": severity_id,
            "time": timestamp_ms,
            "status_id": 1 if event.decision == "success" else 2,
            "actor": {
                "user": {
                    "uid": event.actor_user_id or "",
                    "name": event.metadata.get("user_agent", ""),
                }
            },
            "resources": [
                {
                    "type": "web",
                    "name": event.resource,
                }
            ],
            "metadata": {
                "product": {"name": "hecate", "vendor_name": "hecate"},
                "version": "1.0",
                "original_time": event.timestamp.isoformat(),
            },
            **{k: v for k, v in event.metadata.items() if v is not None},
        }

    def _map_authorization(self, event: SecurityEvent, severity_id: int, timestamp_ms: int) -> dict[str, Any]:
        """Map tool decision event to OCSF Authorization class (2201)."""
        decision_map = {
            "allow": 1,  # Granted
            "deny": 2,  # Denied
            "sandbox": 3,  # Conditional
            "approval_required": 3,
            "require_approval": 3,
        }
        decision_id = decision_map.get(event.decision.lower(), 99)

        return {
            "class_uid": 2201,
            "class_name": "Authorization",
            "activity_name": f"tool_{event.decision}",
            "severity_id": severity_id,
            "time": timestamp_ms,
            "decision": event.decision,
            "decision_id": decision_id,
            "actor": {
                "agent": {
                    "uid": event.actor_agent_id or "",
                },
                "user": {
                    "uid": event.actor_user_id or "",
                },
            },
            "resources": [
                {
                    "type": "tool",
                    "name": event.resource,
                }
            ],
            "policy": {
                "uid": event.metadata.get("policy_version", ""),
                "name": "tool_access_policy",
                "version": event.metadata.get("policy_version", ""),
            },
            "metadata": {
                "product": {"name": "hecate", "vendor_name": "hecate"},
                "version": "1.0",
            },
        }

    def _map_finding(self, event: SecurityEvent, severity_id: int, timestamp_ms: int) -> dict[str, Any]:
        """Map anomaly event to OCSF Security Finding class (2001)."""
        return {
            "class_uid": 2001,
            "class_name": "Security Finding",
            "activity_name": "detect",
            "severity_id": severity_id,
            "time": timestamp_ms,
            "finding_info": {
                "title": event.metadata.get("message", event.action),
                "uid": event.resource,
                "data_sources": [event.source],
                "types": [event.action],
            },
            "resources": [],
            "actor": {
                "user": {
                    "uid": event.actor_user_id or "",
                }
            },
            "metadata": {
                "product": {"name": "hecate", "vendor_name": "hecate"},
                "version": "1.0",
                "original_time": event.timestamp.isoformat(),
            },
            **{k: v for k, v in event.metadata.items() if v is not None},
        }

    async def export(self, events: list[SecurityEvent]) -> None:
        """Transform events to OCSF format and delegate to wrapped exporter.

        Creates a list of OCSF-compliant events and passes them to the
        wrapped exporter as a new SecurityEvent-like batch.

        Since SIEMExporter.export() expects SecurityEvent objects, we
        create lightweight wrapper events with OCSF data in metadata.
        """
        ocsf_events: list[SecurityEvent] = []
        for event in events:
            ocsf_data = self._transform(event)
            # Create a synthetic SecurityEvent with OCSF payload in metadata
            wrapper = SecurityEvent(
                event_type=event.event_type,
                severity=event.severity,
                source=event.source,
                timestamp=event.timestamp,
                actor_user_id=event.actor_user_id,
                actor_agent_id=event.actor_agent_id,
                action=event.action,
                decision=event.decision,
                resource=event.resource,
                metadata={"ocsf": ocsf_data},
            )
            ocsf_events.append(wrapper)

        await self._wrapped.export(ocsf_events)
