"""Alert rule CRUD + evaluator + signal providers.

Three files: ``service.py`` (rule CRUD + dispatch), ``evaluator.py``
(background evaluation task), ``signal_provider.py`` (metric /
event / log sources that feed alerts).
"""

from __future__ import annotations

from hecate.ops.alerts.service import AlertService
from hecate.ops.alerts.signal_provider import SignalProvider

__all__ = ["AlertService", "SignalProvider"]
