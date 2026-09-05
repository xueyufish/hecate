"""Channel-domain admin HTTP endpoints.

Alert rules / events / silences / notification channels / escalation
policies for IM-channel alerting. Relocated from the global
``src/hecate/api/management/`` layer during Phase R-complete; that
global layer no longer exists (the other former members live under
their own domains' ``api/`` directories since #121 / #122).

The alerting business logic stays in ``ops/alerts`` — cross-domain
access from this package is function-level lazy import only (the
sanctioned pattern; enforced by ``tests/test_layering_domain.py``).
"""

from __future__ import annotations
