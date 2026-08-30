"""hecate-memory domain package.

Memory, RAG, and context engineering — extracted from the Hecate core package
as part of the package-split plan (PR2.1).

Registered as the default backend under the ``hecate.memory_providers``
entry-point group as ``builtin`` (see ``provider.py``). The core package
discovers this entry point via ``importlib.metadata`` when
``HECATE_MEMORY_PROVIDER`` is unset or set to ``"builtin"``. Third-party
memory packages (e.g. ``hecate-memory-mem0``) register additional entries
under the same group.
"""
