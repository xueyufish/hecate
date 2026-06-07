"""Tool management commands.

Provides:
- hecate tool list
- hecate tool get <id>
"""

from __future__ import annotations

from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import display_result

app = typer.Typer(no_args_is_help=True)


@app.command()
def list(
    source: Annotated[str | None, typer.Option("--source", help="Filter by source: builtin, custom, mcp")] = None,
    page: Annotated[int, typer.Option("--page", help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Items per page")] = 20,
) -> None:
    """List all tools."""
    client = HecateClient(get_profile_name())

    params: dict = {"page": page, "page_size": page_size}
    if source:
        params["source"] = source

    result = client.get("/api/tools", params=params)
    display_result(result, get_output_format(), columns=["id", "name", "description", "source"], title="Tools")


@app.command()
def get(
    tool_id: Annotated[str, typer.Argument(help="Tool UUID")],
) -> None:
    """Get tool details."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/tools/{tool_id}")
    display_result(result, get_output_format(), title="Tool Details")
