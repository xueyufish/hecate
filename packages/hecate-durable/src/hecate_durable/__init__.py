"""Durable execution core - independently installable persistence package.

Home of the durable-execution contract cluster (``hecate_durable.contracts``),
the language-neutral seams (``hecate_durable.seams``), their InMemory doubles
(``hecate_durable.stub``), and the SQL reference storage with lease/fencing
and event-cursor semantics (``hecate_durable.storage``).

The package depends only on SQLAlchemy: the standalone host wheel closure
must never pull the full ``hecate`` application. Platform consumers keep
importing via the ``hecate.contracts.execution.*`` / ``hecate.execution``
forwarding shims; both resolve here.
"""

from hecate_durable import contracts, seams, storage, stub

__all__ = ["contracts", "seams", "storage", "stub"]
