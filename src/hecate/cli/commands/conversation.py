"""Conversation management commands.

Provides:
- hecate conversation list
- hecate conversation get <id>
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
    page: Annotated[int, typer.Option("--page", help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Items per page")] = 20,
) -> None:
    """List all conversations."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/conversations", params={"page": page, "page_size": page_size})
    display_result(
        result,
        get_output_format(),
        columns=["id", "agent_id", "title", "created_at"],
        title="Conversations",
    )


@app.command()
def get(
    conversation_id: Annotated[str, typer.Argument(help="Conversation UUID")],
) -> None:
    """Get conversation with messages."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/conversations/{conversation_id}")
    display_result(result, get_output_format(), title="Conversation Details")
