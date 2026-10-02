"""Temporal-backed platform execution access for Hecate.

Platform-side integration between the runtime kernel (`hecate_runtime`) and
Temporal: the run worker, the worker pool, and the distributed workflow
adapter. Moved out of the runtime kernel in step5b
(`runtime-standalone-distribution`) — the kernel ships without a Temporal
dependency; channel-conflict semantics (`ConflictResolver`) live in the
kernel as `hecate_runtime.conflict`.
"""
