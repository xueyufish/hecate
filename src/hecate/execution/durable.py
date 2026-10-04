"""Compatibility shim: implementation moved to ``hecate_durable.seams`` (step6).

Forwarding only — the durable-execution seams live in the independently
installable ``hecate-durable`` package (``hecate_durable.seams``).
"""

import sys

import hecate_durable.seams as _mod

sys.modules[__name__] = _mod
