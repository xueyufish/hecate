"""Compatibility shim: implementation moved to ``hecate_durable.stub`` (step6).

Forwarding only — the InMemory doubles live in the independently installable
``hecate-durable`` package (``hecate_durable.stub``).
"""

import sys

import hecate_durable.stub as _mod

sys.modules[__name__] = _mod
