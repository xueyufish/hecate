"""Built-in tool executor — implements core tools for Hecate Agents.

Provides the file-system tools (``web_search``, ``read_file``, ``write_file``,
``list_files``), the sandbox code execution tool (``execute_code``), and the
browser automation tools (6.27 — ``browser_navigate`` / ``browser_click`` /
``browser_type`` / ``browser_extract`` / ``browser_screenshot`` /
``browser_fill_form``). Each tool has a JSON Schema definition for LLM
function calling and an execution function that performs the actual work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hecate.tools.tool.search import SearchProvider

logger = logging.getLogger(__name__)

# JSON Schema definitions for each built-in tool.
# Used by seed_builtin_tools() to populate the DB and by
# format_tools_for_llm() to present to the LLM.
BUILTIN_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "web_search": {
        "description": (
            "Search the web for information. Returns a list of results with title, URL, and snippet for each result."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    "load_skill": {
        "description": (
            "Load the full instructions of a skill advertised in your skills catalog. "
            "Call this before applying a skill; the returned content is valid for the current run."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to load, exactly as advertised in the catalog",
                },
            },
            "required": ["skill_name"],
        },
    },
    "read_file": {
        "description": ("Read the contents of a file at the given path relative to the workspace root."),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the workspace",
                },
            },
            "required": ["path"],
        },
    },
    "recall": {
        "description": (
            "Load a previously offloaded conversation block back into context by its "
            "offload file path (from a '[Earlier conversation offloaded ...]' notice). "
            "Returns the full offloaded messages as JSON."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Offload file path from the offload notice (memory/sessions/.../offloaded_...json)",
                },
            },
            "required": ["path"],
        },
    },
    "write_file": {
        "description": (
            "Write content to a file at the given path relative to the workspace root. "
            "Creates parent directories if they do not exist."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path to the file within the workspace",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    "list_files": {
        "description": (
            "List files and directories at the given path relative to the workspace root. "
            "Defaults to the workspace root if no path is provided."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path within the workspace (default: root)",
                    "default": ".",
                },
            },
        },
    },
    "execute_code": {
        "description": ("Execute Python code in a sandboxed Docker container. Returns stdout, stderr, and exit code."),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code to execute",
                },
            },
            "required": ["code"],
        },
    },
    "browser_navigate": {
        "description": (
            "Navigate the agent's per-session browser to a URL. Returns the final URL, page title, "
            "and HTTP status. The URL must be in the agent environment's allowedDomains list, otherwise "
            "the tool refuses the navigation."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The absolute URL to navigate to (must be http/https)",
                },
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "description": "When to consider navigation complete (default: load)",
                    "default": "load",
                },
            },
            "required": ["url"],
        },
    },
    "browser_click": {
        "description": (
            "Click an element on the current page. Either ``selector`` (CSS) or ``text`` (visible "
            "text) must be provided. When text matches multiple elements, ``index`` disambiguates."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the element"},
                "text": {"type": "string", "description": "Visible text to match"},
                "index": {
                    "type": "integer",
                    "description": "When multiple elements match, which one to click (default 0)",
                    "default": 0,
                },
            },
        },
    },
    "browser_type": {
        "description": (
            "Type text into an input element. Clears the existing content first; pass "
            "``submit=true`` to press Enter at the end."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the input"},
                "text": {"type": "string", "description": "Text to type"},
                "submit": {
                    "type": "boolean",
                    "description": "Press Enter after typing (default false)",
                    "default": False,
                },
            },
            "required": ["selector", "text"],
        },
    },
    "browser_extract": {
        "description": (
            "Extract content from the page or a specific element. ``mode`` defaults to ``a11y`` "
            "(accessibility tree, LLM-friendly structured text); ``text`` returns visible text; "
            "``html`` returns raw outer HTML."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "Optional CSS selector to scope the extraction"},
                "mode": {
                    "type": "string",
                    "enum": ["a11y", "text", "html"],
                    "description": "Extraction mode (default a11y)",
                    "default": "a11y",
                },
            },
        },
    },
    "browser_screenshot": {
        "description": (
            "Capture a screenshot of the current page. ``full_page`` captures the entire scrollable "
            "page; ``selector`` captures a single element. Returns base64-encoded PNG plus current URL."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "full_page": {
                    "type": "boolean",
                    "description": "Capture the full scrollable page (default false)",
                    "default": False,
                },
                "selector": {"type": "string", "description": "Optional CSS selector of element to capture"},
            },
        },
    },
    "browser_fill_form": {
        "description": (
            "Atomically fill multiple form fields. Each field has a ``selector`` and a ``value``. "
            "Returns per-field success status; partial failures set ``partial: true``."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "selector": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["selector", "value"],
                    },
                    "description": "List of {selector, value} pairs to fill",
                },
            },
            "required": ["fields"],
        },
    },
    # -- Memory tools (agent-memory-tools). Gated by MEMORY_TOOLS_ENABLED at
    # seeding time; conversation_search additionally by RECALL_INDEXING_ENABLED.
    "memory_replace": {
        "description": (
            "Replace an exact string inside one of your memory blocks (persistent notes shown to "
            "you every turn). old_string must appear exactly once; empty new_string deletes the "
            "fragment. The block is never silently truncated — growing it past its limit errors."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Memory block label (e.g. persona)"},
                "old_string": {"type": "string", "description": "Exact text to replace (must match once)"},
                "new_string": {"type": "string", "description": "Replacement text (empty string deletes)"},
            },
            "required": ["label", "old_string", "new_string"],
        },
    },
    "memory_insert": {
        "description": (
            "Insert a line into one of your memory blocks. insert_line=-1 (default) appends at the "
            "end; 0 inserts at the top; N inserts after line N. Never include line-number prefixes."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Memory block label"},
                "insert_text": {"type": "string", "description": "The line to insert"},
                "insert_line": {
                    "type": "integer",
                    "description": "Insert after this line (-1 = append at end, 0 = top)",
                    "default": -1,
                },
            },
            "required": ["label", "insert_text"],
        },
    },
    "memory_rethink": {
        "description": (
            "Rewrite one of your memory blocks wholesale. Use for sweeping changes when precise "
            "edits would be tedious; for small corrections prefer memory_replace. The block must "
            "already exist."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Memory block label"},
                "new_content": {"type": "string", "description": "The full new block content"},
            },
            "required": ["label", "new_content"],
        },
    },
    "memory_search": {
        "description": (
            "Search your long-term memories (user memories and knowledge facts) for information "
            "relevant to a query. Returns ranked facts with their source layer and revision. The "
            "optional tier argument routes to different memory surfaces: tier_2 (default; L3 user "
            "memories + L4 knowledge facts), tier_4 (reflections; requires REFLECTION_ENABLED), "
            "tier_5 (workspace-shared / team-shared facts; requires REFLECTION_ENABLED). When the "
            "underlying provider doesn't declare the requested tier, the tool returns a structured "
            "error rather than crashing."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "top_k": {"type": "integer", "description": "Max results (default 5, max 20)", "default": 5},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tag filter (knowledge memories)",
                },
                "tier": {
                    "type": "string",
                    "enum": ["tier_1", "tier_2", "tier_3", "tier_4", "tier_5"],
                    "description": (
                        "Memory tier to search. tier_2 is the default (L3 + L4 facts). tier_4 routes "
                        "to reflections / work context graph (4.21 surfaces). tier_5 routes to "
                        "workspace-shared / team-shared facts (4.23 surfaces). Other tiers return "
                        "structured errors when the active provider doesn't declare the matching "
                        "capability."
                    ),
                    "default": "tier_2",
                },
            },
            "required": ["query"],
        },
    },
    "memory_add": {
        "description": (
            "Store a durable knowledge fact for future conversations. Check memory_search first and "
            "use memory_update for near-duplicates instead of creating a redundant entry. The "
            "optional scope argument selects the namespace: actor_scoped (default; visible only to "
            "the calling actor) or workspace_shared (visible to every actor in the workspace; "
            "requires workspace admin role)."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The fact to store, one complete sentence"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional categorization tags"},
                "importance": {
                    "type": "number",
                    "description": "Importance score 0.0-1.0 (default 0.5)",
                    "default": 0.5,
                },
                "scope": {
                    "type": "string",
                    "enum": ["actor_scoped", "workspace_shared"],
                    "description": (
                        "Namespace the new fact is written into. actor_scoped (default) is visible "
                        "only to the calling actor within the workspace. workspace_shared promotes "
                        "the fact into the workspace-wide pool and requires workspace admin role; "
                        "editor-role callers receive a structured permission_denied error."
                    ),
                    "default": "actor_scoped",
                },
            },
            "required": ["content"],
        },
    },
    "memory_update": {
        "description": (
            "Correct an existing memory entry in place. Pass expected_revision (from memory_search "
            "or the previous write) to refuse the update when someone else changed it meanwhile."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of the memory entry to update"},
                "content": {"type": "string", "description": "New content"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "New tags"},
                "importance": {"type": "number", "description": "New importance 0.0-1.0"},
                "expected_revision": {
                    "type": "integer",
                    "description": "Refuse the update unless the current revision matches",
                },
            },
            "required": ["memory_id"],
        },
    },
    "memory_forget": {
        "description": (
            "Retire a memory entry that is wrong or no longer applies. Soft-deleted: the record is "
            "kept for audit but never returned by searches again."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "ID of the memory entry to forget"},
                "expected_revision": {
                    "type": "integer",
                    "description": "Refuse the deletion unless the current revision matches",
                },
            },
            "required": ["memory_id"],
        },
    },
    "conversation_search": {
        "description": (
            "Search your past conversations with this user (across sessions). Returns message "
            "excerpts with session pointers. If results are weak, reformulate the query, narrow "
            "the date window, or exclude already-inspected sessions via exclude_session_ids."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for in past conversations"},
                "limit": {"type": "integer", "description": "Max messages (default 5, max 20)", "default": 5},
                "start_date": {
                    "type": "string",
                    "description": "Only messages after this ISO-8601 timestamp",
                },
                "end_date": {
                    "type": "string",
                    "description": "Only messages before this ISO-8601 timestamp",
                },
                "roles": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["user", "assistant"]},
                    "description": "Filter by message role (default: both)",
                },
                "cursor": {
                    "type": "string",
                    "description": "Opaque continuation token from a previous result page",
                },
                "exclude_session_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Session IDs to skip (from previous result pages)",
                },
            },
            "required": ["query"],
        },
    },
    # 4.21 reflection_tools. Visible only when REFLECTION_ENABLED=true at
    # seeding time; the seeding layer consults
    # ``tools_backend.get_visible_memory_tool_names`` to decide which of
    # these ten tool names are mounted.
    "reflection_search": {
        "description": (
            "Search your task reflections — durable lessons learned from past task execution. "
            "Returns only approved reflections; pending / rejected / deprecated rows are filtered "
            "out. Each hit carries the original task_type tags so the agent can filter by use case. "
            "Available only when REFLECTION_ENABLED is on."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "task_type": {
                    "type": "string",
                    "description": "Optional task_type tag (matches against reflection.use_cases)",
                },
                "top_k": {"type": "integer", "description": "Max results (default 5, max 20)", "default": 5},
            },
            "required": ["query"],
        },
    },
    "work_context_query": {
        "description": (
            "Query the Work Context Graph (KM6 / 4.21 enhancement). Returns active nodes that "
            "match the query and (optionally) the requested node_type. Inactive / superseded "
            "nodes are excluded. Available only when REFLECTION_ENABLED is on."
        ),
        "risk_level": "LOW",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "node_type": {
                    "type": "string",
                    "enum": ["method", "outcome", "correction", "source", "pattern"],
                    "description": "Optional node type filter",
                },
                "top_k": {"type": "integer", "description": "Max results (default 5, max 20)", "default": 5},
            },
            "required": ["query"],
        },
    },
}


_BROWSER_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_click",
        "browser_type",
        "browser_extract",
        "browser_screenshot",
        "browser_fill_form",
    }
)

# Memory tools route to the MemoryToolBackend (hecate-memory) instead of the
# generic executor branches.
_MEMORY_TOOLS = frozenset(
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
        # ``REFLECTION_ENABLED=false`` so the agent never sees a tool
        # whose backend would refuse the call.
        "reflection_search",
        "work_context_query",
    }
)


def get_browser_tool_names() -> frozenset[str]:
    """Return the set of tool names handled by the browser subsystem."""
    return _BROWSER_TOOLS


def get_memory_tool_names() -> frozenset[str]:
    """Return the set of tool names handled by the memory backend."""
    return _MEMORY_TOOLS


def get_risk_level(tool_name: str) -> str:
    """Return the static risk_level for a builtin tool name (defaults to LOW)."""
    definition = BUILTIN_TOOL_DEFINITIONS.get(tool_name)
    if definition is None:
        return "LOW"
    return str(definition.get("risk_level", "LOW")).upper()


class BuiltInToolExecutor:
    """Executes built-in tools by routing to the appropriate handler.

    Args:
        search_provider: The search provider for the web_search tool.
        workspace_root: Root directory for file operations (default: "./workspace").
        browser_session_manager: Optional :class:`BrowserSessionManager` for
            ``browser_*`` tools. When ``None``, browser tools return
            ``browser_disabled``.
        allowed_domains: Optional list of allowed domain patterns for the
            browser subsystem. Empty list means fail-closed (no navigation
            permitted). ``None`` means fall back to the agent environment's
            ``allowedDomains``.
    """

    def __init__(
        self,
        search_provider: SearchProvider,
        workspace_root: str = "./workspace",
        *,
        browser_session_manager: Any | None = None,
        allowed_domains: list[str] | None = None,
        skill_loader: Any | None = None,
        memory_backend: Any | None = None,
    ) -> None:
        self._search = search_provider
        self._workspace = Path(workspace_root).resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._browser_session_manager = browser_session_manager
        self._allowed_domains = allowed_domains if allowed_domains is not None else []
        # SkillLoader for the load_skill tool (L2 progressive disclosure).
        # None in paths without agent context (e.g. the MCP server surface):
        # load_skill then fails closed with an informative error.
        self._skill_loader = skill_loader
        # MemoryToolBackend for the memory_* / conversation_search tools
        # (agent-memory-tools). None → memory tools fail closed with an
        # informative error instead of acting without a scope.
        self._memory_backend = memory_backend

    async def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
        """Execute a built-in tool by name.

        Args:
            name: Tool name (must be a key in BUILTIN_TOOL_DEFINITIONS).
            args: Tool arguments.
            context: Optional execution context. For execute_code, may
                contain ``_sandbox_volumes`` (dict[str, str]) for environment
                mounting. For browser_* tools, may contain ``session_id``
                (str) identifying the agent session. For load_skill, must
                contain ``agent_id`` and ``workspace_id`` (UUID strings) so
                the catalog membership check can be enforced.

        Returns:
            Tool-specific result.

        Raises:
            ValueError: If the tool name is unknown.
        """
        if name in _BROWSER_TOOLS:
            return await self._dispatch_browser(name, args, context)

        if name in _MEMORY_TOOLS:
            if self._memory_backend is None:
                return {
                    "ok": False,
                    "error": "unavailable",
                    "detail": "memory tools are not enabled in this execution path",
                }
            return await self._memory_backend.execute(name, args, context)

        if name == "execute_code":
            return await self._execute_code(args, context)

        if name == "load_skill":
            return await self._load_skill(args, context)

        if name == "recall":
            return await self._recall(args, context)

        handler = {
            "web_search": self._web_search,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "list_files": self._list_files,
        }.get(name)
        if handler is None:
            raise ValueError(f"Unknown built-in tool: {name!r}")
        return await handler(args)

    async def _load_skill(self, args: dict[str, Any], context: dict[str, Any] | None) -> str:
        """Serve full L2 skill content for an advertised skill."""
        if self._skill_loader is None:
            raise ValueError("load_skill is not available in this execution path")
        context = context or {}
        workspace_id = context.get("workspace_id")
        agent_id = context.get("agent_id")
        if not workspace_id or not agent_id:
            raise ValueError("load_skill requires agent context (agent_id, workspace_id)")

        import uuid as _uuid

        from hecate.tools.skill.loader import SkillNotAdvertisedError

        skill_name = str(args.get("skill_name", "")).strip()
        if not skill_name:
            raise ValueError("load_skill requires a non-empty skill_name")
        session_id = context.get("session_id")
        try:
            return await self._skill_loader.load_skill_content(
                skill_name,
                _uuid.UUID(str(agent_id)),
                _uuid.UUID(str(workspace_id)),
                _uuid.UUID(str(session_id)) if session_id else None,
            )
        except SkillNotAdvertisedError as exc:
            raise ValueError(str(exc)) from exc

    async def _recall(self, args: dict[str, Any], context: dict[str, Any] | None) -> str:
        """Reload an offloaded conversation block into context (4.13).

        The agent environment rides the per-call tool context (threaded from
        the runtime execution context by ToolWorker); read-only — never
        mutates the channel history, checkpoints, or the offloaded file.
        """
        context = context or {}
        environment = context.get("environment")
        if environment is None:
            raise ValueError("recall is not available in this execution path (no agent environment)")
        session_id = context.get("session_id")
        if not session_id:
            raise ValueError("recall requires a session context")
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("recall requires a non-empty path")
        if not str(path).startswith(f"memory/sessions/{session_id}/"):
            raise ValueError(f"recall path is not part of this session's offload storage: {path!r}")
        if not await environment.exists(path):
            raise FileNotFoundError(f"Offload file not found: {path}")
        import json

        raw = await environment.read_file(path)
        messages = json.loads(raw.decode("utf-8"))
        return json.dumps({"path": path, "messages": messages}, ensure_ascii=False, indent=2)

    def _resolve_and_validate_path(self, rel_path: str) -> Path:
        resolved = (self._workspace / rel_path).resolve()
        if not resolved.is_relative_to(self._workspace):
            raise ValueError(f"Path traversal detected: {rel_path!r} resolves outside workspace")
        return resolved

    async def _web_search(self, args: dict[str, Any]) -> list[dict]:
        query = args["query"]
        max_results = args.get("max_results", 5)
        return await self._search.search(query, max_results=max_results)

    async def _read_file(self, args: dict[str, Any]) -> str:
        path = self._resolve_and_validate_path(args["path"])
        if not path.exists():
            raise FileNotFoundError(f"File not found: {args['path']}")
        return path.read_text(encoding="utf-8")

    async def _write_file(self, args: dict[str, Any]) -> str:
        path = self._resolve_and_validate_path(args["path"])
        content = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {args['path']}"

    async def _list_files(self, args: dict[str, Any]) -> list[str]:
        rel_path = args.get("path", ".")
        path = self._resolve_and_validate_path(rel_path)
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {rel_path}")
        if not path.is_dir():
            raise ValueError(f"Not a directory: {rel_path}")
        return sorted(entry.name for entry in path.iterdir())

    async def _execute_code(
        self,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute code tool via SandboxPool (when enabled) or SandboxExecutor."""
        code = args["code"]
        try:
            from hecate_sandbox.sandbox.executor import SandboxConfig, SandboxExecutor
        except ImportError:
            return {
                "stdout": "",
                "stderr": "Sandbox unavailable: Docker is not configured",
                "exit_code": -1,
                "timed_out": False,
            }

        volumes: dict[str, str] = {}
        if context:
            volumes = context.get("_sandbox_volumes", {})

        cfg = SandboxConfig(volumes=volumes)

        from hecate_sandbox.sandbox import get_sandbox_pool

        pool = get_sandbox_pool()
        if pool is not None:
            result = await pool.execute("execute_code", {"code": code}, cfg)
        else:
            executor = SandboxExecutor(config=cfg)
            result = await executor.execute(tool_name="execute_code", args={"code": code})

        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }

    async def _dispatch_browser(
        self,
        name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Route a browser_* tool to its session-bound handler.

        Browser tools execute against a per-agent-session :class:`BrowserSession`.
        The session id is supplied via ``context['session_id']``; without it,
        a fresh session id is derived from the current agent id when available.
        """
        from hecate.core.config import settings

        if str(getattr(settings, "AGENT_ENV_BACKEND", "")).lower() == "local":
            return {"error": "browser_disabled", "reason": "sandbox_required"}
        if self._browser_session_manager is None:
            return {"error": "browser_disabled", "reason": "session_manager_unconfigured"}

        context = context or {}
        session_id = str(context.get("session_id") or context.get("agent_id") or "default")

        if name == "browser_navigate":
            url = args.get("url", "")
            allowed = self._allowed_domains_for(context)
            from hecate_sandbox.environment.network_policy import is_url_allowed

            if not is_url_allowed(url, allowed):
                return {
                    "error": "domain_not_allowed",
                    "url": url,
                    "allowed_domains": allowed,
                    "risk_level": "HIGH",
                }
            session = await self._browser_session_manager.get_or_create(session_id)
            return await session.navigate(args["url"], args.get("wait_until", "load"))

        session = await self._browser_session_manager.get_or_create(session_id)
        if name == "browser_click":
            return await session.click(
                selector=args.get("selector"),
                text=args.get("text"),
                index=int(args.get("index", 0)),
            )
        if name == "browser_type":
            return await session.type_text(args["selector"], args["text"], submit=bool(args.get("submit", False)))
        if name == "browser_extract":
            return await session.extract(selector=args.get("selector"), mode=args.get("mode", "a11y"))
        if name == "browser_screenshot":
            return await session.screenshot(
                full_page=bool(args.get("full_page", False)),
                selector=args.get("selector"),
            )
        if name == "browser_fill_form":
            return await session.fill_form(args.get("fields") or [])
        return {"error": "unknown_browser_tool", "tool": name}

    def _allowed_domains_for(self, context: dict[str, Any]) -> list[str]:
        """Resolve the effective allowed-domains list for a given call."""
        if "allowed_domains" in context:
            return list(context["allowed_domains"])
        return self._allowed_domains
