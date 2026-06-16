"""Output formatting utilities for the CLI.

Provides table rendering (via rich) and JSON output modes,
plus error display and confirmation prompts.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()


def format_table(
    items: list[dict[str, Any]],
    columns: list[str],
    title: str | None = None,
) -> None:
    """Render items as a rich table.

    Args:
        items: List of dicts to display.
        columns: Column names to include (keys into each dict).
        title: Optional table title.
    """
    table = Table(title=title, show_lines=False, expand=False)

    for col in columns:
        table.add_column(col, overflow="fold")

    for item in items:
        row = []
        for col in columns:
            val = item.get(col, "")
            if val is None:
                val = ""
            elif isinstance(val, dict | list):
                val = json.dumps(val, ensure_ascii=False)
            else:
                val = str(val)
            # Truncate long values
            if len(val) > 80:
                val = val[:77] + "..."
            row.append(val)
        table.add_row(*row)

    console.print(table)


def format_json(data: Any) -> None:
    """Print data as formatted JSON.

    Args:
        data: Any JSON-serializable value.
    """
    console.print(json.dumps(data, indent=2, ensure_ascii=False))


def display_result(
    data: Any,
    output_format: str = "table",
    columns: list[str] | None = None,
    title: str | None = None,
) -> None:
    """Display API result in the specified format.

    Args:
        data: API response data.
        output_format: "table" or "json".
        columns: Column names for table mode.
        title: Optional table title.
    """
    if output_format == "json":
        format_json(data)
        return

    # Table mode
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Check for paginated response
        if "items" in data:
            items = data["items"]
            total = data.get("total", len(items))
            console.print(f"[dim]Total: {total}[/dim]")
        else:
            # Single item — show as key-value pairs
            if columns is None:
                columns = list(data.keys())
            items = [data]
    else:
        console.print(str(data))
        return

    if columns is None:
        if items:
            columns = list(items[0].keys())
        else:
            console.print("[dim]No results.[/dim]")
            return

    if not items:
        console.print("[dim]No results.[/dim]")
        return

    format_table(items, columns, title=title)


def display_error(message: str) -> None:
    """Display an error message and exit.

    Args:
        message: Error description.
    """
    console.print(f"[red]Error:[/red] {message}")
    sys.exit(1)


def confirm_delete(resource_type: str, resource_id: str) -> bool:
    """Prompt for confirmation before deleting a resource.

    Args:
        resource_type: Type of resource (e.g., "agent").
        resource_id: ID of the resource.

    Returns:
        True if user confirmed, False otherwise.
    """
    from rich.prompt import Confirm

    return Confirm.ask(
        f"Delete {resource_type} {resource_id}?",
        default=False,
    )
