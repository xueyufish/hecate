"""Built-in tool executor — implements 5 core tools for Hecate Agents.

Provides web_search, read_file, write_file, list_files, and execute_code
tools. Each tool has a JSON Schema definition for LLM function calling
and an execution function that performs the actual work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hecate.services.tool.search import SearchProvider

logger = logging.getLogger(__name__)

# JSON Schema definitions for each built-in tool.
# Used by seed_builtin_tools() to populate the DB and by
# format_tools_for_llm() to present to the LLM.
BUILTIN_TOOL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "web_search": {
        "description": (
            "Search the web for information. Returns a list of results with title, URL, and snippet for each result."
        ),
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
}


class BuiltInToolExecutor:
    """Executes built-in tools by routing to the appropriate handler.

    Args:
        search_provider: The search provider for the web_search tool.
        workspace_root: Root directory for file operations (default: "./workspace").
    """

    def __init__(
        self,
        search_provider: SearchProvider,
        workspace_root: str = "./workspace",
    ) -> None:
        self._search = search_provider
        self._workspace = Path(workspace_root).resolve()
        self._workspace.mkdir(parents=True, exist_ok=True)

    async def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a built-in tool by name.

        Args:
            name: Tool name (must be a key in BUILTIN_TOOL_DEFINITIONS).
            args: Tool arguments.

        Returns:
            Tool-specific result.

        Raises:
            ValueError: If the tool name is unknown.
        """
        handler = {
            "web_search": self._web_search,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "list_files": self._list_files,
            "execute_code": self._execute_code,
        }.get(name)

        if handler is None:
            raise ValueError(f"Unknown built-in tool: {name!r}")

        return await handler(args)

    def _resolve_and_validate_path(self, rel_path: str) -> Path:
        """Resolve a relative path against workspace root and validate it's inside.

        Args:
            rel_path: Relative path string.

        Returns:
            Resolved absolute Path.

        Raises:
            ValueError: If the resolved path is outside the workspace.
        """
        resolved = (self._workspace / rel_path).resolve()
        if not resolved.is_relative_to(self._workspace):
            raise ValueError(f"Path traversal detected: {rel_path!r} resolves outside workspace")
        return resolved

    async def _web_search(self, args: dict[str, Any]) -> list[dict]:
        """Execute web_search tool."""
        query = args["query"]
        max_results = args.get("max_results", 5)
        return await self._search.search(query, max_results=max_results)

    async def _read_file(self, args: dict[str, Any]) -> str:
        """Execute read_file tool."""
        path = self._resolve_and_validate_path(args["path"])
        if not path.exists():
            raise FileNotFoundError(f"File not found: {args['path']}")
        return path.read_text(encoding="utf-8")

    async def _write_file(self, args: dict[str, Any]) -> str:
        """Execute write_file tool."""
        path = self._resolve_and_validate_path(args["path"])
        content = args["content"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Written {len(content)} bytes to {args['path']}"

    async def _list_files(self, args: dict[str, Any]) -> list[str]:
        """Execute list_files tool."""
        rel_path = args.get("path", ".")
        path = self._resolve_and_validate_path(rel_path)
        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {rel_path}")
        if not path.is_dir():
            raise ValueError(f"Not a directory: {rel_path}")
        return sorted(entry.name for entry in path.iterdir())

    async def _execute_code(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute code tool via SandboxExecutor."""
        code = args["code"]
        try:
            from hecate.services.sandbox.executor import SandboxExecutor
        except ImportError:
            return {
                "stdout": "",
                "stderr": "Sandbox unavailable: Docker is not configured",
                "exit_code": -1,
                "timed_out": False,
            }

        executor = SandboxExecutor()
        result = await executor.execute(tool_name="execute_code", args={"code": code})
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
        }
