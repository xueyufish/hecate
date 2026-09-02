"""Event state store services — PostgreSQL implementation + factory.

Public API exports the production backend plus the factory helper. The
engine-layer ``EventStore`` ABC (``hecate.runtime.eventstore``) remains the
import path for the abstract interface; this package provides the concrete
persistence implementation and the factory.
"""

from __future__ import annotations

from hecate.services.event_state.factory import SUPPORTED_BACKENDS, create_event_store
from hecate.services.event_state.models import EventModel
from hecate.services.event_state.postgres_store import PostgresEventStore

__all__ = [
    "EventModel",
    "PostgresEventStore",
    "SUPPORTED_BACKENDS",
    "create_event_store",
]
