"""Agent management commands.

Provides:
- hecate agent list
- hecate agent create
- hecate agent get <id>
- hecate agent update <id>
- hecate agent delete <id>
"""

from __future__ import annotations

from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import confirm_delete, display_result

app = typer.Typer(no_args_is_help=True)


@app.command()
def list(
    page: Annotated[int, typer.Option("--page", help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Items per page")] = 20,
) -> None:
    """List all agents."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/agents", params={"page": page, "page_size": page_size})
    display_result(result, get_output_format(), columns=["id", "name", "mode", "model_config"], title="Agents")


@app.command()
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Agent name")],
    model: Annotated[str, typer.Option("--model", "-m", help="Model identifier (e.g., gpt-4o)")],
    mode: Annotated[str, typer.Option("--mode", help="Agent mode: chat, three_layer, workflow")] = "chat",
    persona: Annotated[str | None, typer.Option("--persona", "-p", help="Agent persona/system prompt")] = None,
    tools: Annotated[str | None, typer.Option("--tools", "-t", help="Comma-separated tool names")] = None,
    kb_ids: Annotated[str | None, typer.Option("--kb-ids", help="Comma-separated knowledge base IDs")] = None,
) -> None:
    """Create a new agent."""
    client = HecateClient(get_profile_name())

    body: dict = {
        "name": name,
        "model_config": {"model": model},
        "mode": mode,
    }
    if persona:
        body["persona"] = persona
    if tools:
        body["tools"] = [t.strip() for t in tools.split(",")]
    if kb_ids:
        body["knowledge_base_ids"] = [k.strip() for k in kb_ids.split(",")]

    result = client.post("/api/agents", json=body)
    display_result(result, get_output_format(), title="Agent Created")


@app.command()
def get(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
) -> None:
    """Get agent details."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/agents/{agent_id}")
    display_result(result, get_output_format(), title="Agent Details")


@app.command()
def update(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
    name: Annotated[str | None, typer.Option("--name", "-n", help="New name")] = None,
    persona: Annotated[str | None, typer.Option("--persona", "-p", help="New persona")] = None,
    tools: Annotated[str | None, typer.Option("--tools", "-t", help="Comma-separated tool names")] = None,
    kb_ids: Annotated[str | None, typer.Option("--kb-ids", help="Comma-separated knowledge base IDs")] = None,
) -> None:
    """Update an existing agent."""
    client = HecateClient(get_profile_name())

    body: dict = {}
    if name:
        body["name"] = name
    if persona:
        body["persona"] = persona
    if tools:
        body["tools"] = [t.strip() for t in tools.split(",")]
    if kb_ids:
        body["knowledge_base_ids"] = [k.strip() for k in kb_ids.split(",")]

    if not body:
        typer.echo("No fields to update. Use --name, --persona, --tools, or --kb-ids.")
        raise typer.Exit(1)

    result = client.put(f"/api/agents/{agent_id}", json=body)
    display_result(result, get_output_format(), title="Agent Updated")


@app.command()
def delete(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete an agent (soft delete)."""
    if not force and not confirm_delete("agent", agent_id):
        raise typer.Abort()

    client = HecateClient(get_profile_name())
    client.delete(f"/api/agents/{agent_id}")
    typer.echo(f"Agent {agent_id} deleted.")
