"""Kernel-owned tool-name vocabulary (step5b).

The memory-tool name set was previously imported from
``hecate.tools.tool.builtin`` inside the ToolWorker's escalation path,
crossing the kernel/platform boundary for a plain string set. The names
are now declared here; the platform ``builtin`` module remains the
implementation owner of those tools and should import this set for its
own grouping so the vocabulary cannot drift.
"""

from __future__ import annotations

MEMORY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "memory_replace",
        "memory_insert",
        "memory_rethink",
        "memory_search",
        "memory_add",
        "memory_update",
        "memory_forget",
        "conversation_search",
        # 4.21 reflection_tools. Seeding layer hides these when
        # reflection is disabled so the agent never sees a tool whose
        # backend would refuse the call.
        "reflection_search",
        "work_context_query",
    }
)
