"""Compatibility shim: implementation moved to ``hecate_runtime`` (step6).

Forwarding only — the action-ledger hook and the shared recovery decision
table live in ``hecate_runtime.action_ledger``.
"""

import sys

import hecate_runtime.action_ledger as _mod

sys.modules[__name__] = _mod
