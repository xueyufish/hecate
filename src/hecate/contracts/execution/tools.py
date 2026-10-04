"""Compatibility shim: implementation moved to ``hecate_durable`` (step6).

Forwarding only — the durable-execution contract cluster, seams, and doubles
live in the independently installable ``hecate-durable`` package so the
standalone host wheel closure never pulls the full ``hecate`` application.
"""

import sys

import hecate_durable.contracts.tools as _mod

sys.modules[__name__] = _mod
