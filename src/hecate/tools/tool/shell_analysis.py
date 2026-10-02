"""Compatibility shim: implementation moved to ``hecate_runtime`` (step5b).

Forwarding only — slated for removal in step19.
"""

from hecate_runtime.shell_analysis import analyze_command, decompose_command

__all__ = ["analyze_command", "decompose_command"]
