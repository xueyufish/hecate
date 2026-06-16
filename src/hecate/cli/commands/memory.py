"""Memory management commands.

Provides:
- hecate memory blocks <agent_id>       — list agent memory blocks
- hecate memory blocks create <agent_id> — create memory block
- hecate memory blocks update <agent_id> <block_id>
- hecate memory blocks delete <agent_id> <block_id>
- hecate memory list                     — list user memories
- hecate memory search <query>           — search user memories
"""

from __future__ import annotations

from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import confirm_delete, display_result

app = typer.Typer(no_args_is_help=True)

# Sub-group for memory blocks
blocks_app = typer.Typer(no_args_is_help=True)
app.add_typer(blocks_app, name="blocks", help="Agent memory blocks (L1)")


@blocks_app.command()
def list(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
) -> None:
    """List memory blocks for an agent."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/agents/{agent_id}/memory-blocks")
    display_result(result, get_output_format(), columns=["id", "label", "content", "updated_at"], title="Memory Blocks")


@blocks_app.command()
def create(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
    label: Annotated[str, typer.Option("--label", "-l", help="Block label")],
    content: Annotated[str, typer.Option("--content", "-c", help="Block content")],
    limit: Annotated[int, typer.Option("--limit", help="Token limit")] = 2000,
) -> None:
    """Create a memory block for an agent."""
    client = HecateClient(get_profile_name())
    body = {"label": label, "content": content, "limit": limit}
    result = client.post(f"/api/agents/{agent_id}/memory-blocks", json=body)
    display_result(result, get_output_format(), title="Memory Block Created")


@blocks_app.command()
def update(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
    block_id: Annotated[str, typer.Argument(help="Memory block UUID")],
    label: Annotated[str | None, typer.Option("--label", "-l", help="New label")] = None,
    content: Annotated[str | None, typer.Option("--content", "-c", help="New content")] = None,
) -> None:
    """Update a memory block."""
    client = HecateClient(get_profile_name())
    body: dict = {}
    if label:
        body["label"] = label
    if content:
        body["content"] = content

    if not body:
        typer.echo("No fields to update. Use --label or --content.")
        raise typer.Exit(1)

    result = client.put(f"/api/agents/{agent_id}/memory-blocks/{block_id}", json=body)
    display_result(result, get_output_format(), title="Memory Block Updated")


@blocks_app.command()
def delete(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
    block_id: Annotated[str, typer.Argument(help="Memory block UUID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a memory block."""
    if not force and not confirm_delete("memory block", block_id):
        raise typer.Abort()

    client = HecateClient(get_profile_name())
    client.delete(f"/api/agents/{agent_id}/memory-blocks/{block_id}")
    typer.echo(f"Memory block {block_id} deleted.")


# User memories
@app.command(name="list")
def list_memories() -> None:
    """List user memories."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/memory")
    display_result(
        result,
        get_output_format(),
        columns=["id", "user_id", "key", "value", "updated_at"],
        title="User Memories",
    )


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query")],
) -> None:
    """Search user memories."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/memory", params={"q": query})
    display_result(result, get_output_format(), title="Search Results")
