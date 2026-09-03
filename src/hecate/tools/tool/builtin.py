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


def get_browser_tool_names() -> frozenset[str]:
    """Return the set of tool names handled by the browser subsystem."""
    return _BROWSER_TOOLS


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
    ) -> None:
        self._search = search_provider
        self._workspace = Path(workspace_root).resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._browser_session_manager = browser_session_manager
        self._allowed_domains = allowed_domains if allowed_domains is not None else []

    async def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> Any:
        """Execute a built-in tool by name.

        Args:
            name: Tool name (must be a key in BUILTIN_TOOL_DEFINITIONS).
            args: Tool arguments.
            context: Optional execution context. For execute_code, may
                contain ``_sandbox_volumes`` (dict[str, str]) for environment
                mounting. For browser_* tools, may contain ``session_id``
                (str) identifying the agent session.

        Returns:
            Tool-specific result.

        Raises:
            ValueError: If the tool name is unknown.
        """
        if name in _BROWSER_TOOLS:
            return await self._dispatch_browser(name, args, context)

        if name == "execute_code":
            return await self._execute_code(args, context)

        handler = {
            "web_search": self._web_search,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "list_files": self._list_files,
        }.get(name)
        if handler is None:
            raise ValueError(f"Unknown built-in tool: {name!r}")
        return await handler(args)

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
