"""Prompt management commands.

Provides:
- hecate prompt list
- hecate prompt create
- hecate prompt get <id>
- hecate prompt update <id>
- hecate prompt delete <id>
- hecate prompt versions <id>
- hecate prompt by-label <label>
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
    """List all prompts."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/prompts", params={"page": page, "page_size": page_size})
    display_result(result, get_output_format(), columns=["id", "name", "created_at"], title="Prompts")


@app.command()
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Prompt name")],
    content: Annotated[str, typer.Option("--content", "-c", help="Prompt content/template")],
    label: Annotated[str | None, typer.Option("--label", "-l", help="Deployment label (e.g., production)")] = None,
) -> None:
    """Create a new prompt with initial version."""
    client = HecateClient(get_profile_name())
    body: dict = {"name": name, "content": content}
    if label:
        body["label"] = label
    result = client.post("/api/prompts", json=body)
    display_result(result, get_output_format(), title="Prompt Created")


@app.command()
def get(
    prompt_id: Annotated[str, typer.Argument(help="Prompt UUID")],
) -> None:
    """Get prompt details."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/prompts/{prompt_id}")
    display_result(result, get_output_format(), title="Prompt Details")


@app.command()
def update(
    prompt_id: Annotated[str, typer.Argument(help="Prompt UUID")],
    name: Annotated[str | None, typer.Option("--name", "-n", help="New name")] = None,
    content: Annotated[str | None, typer.Option("--content", "-c", help="New content")] = None,
    label: Annotated[str | None, typer.Option("--label", "-l", help="New deployment label")] = None,
) -> None:
    """Update an existing prompt."""
    client = HecateClient(get_profile_name())
    body: dict = {}
    if name:
        body["name"] = name
    if content:
        body["content"] = content
    if label:
        body["label"] = label

    if not body:
        typer.echo("No fields to update. Use --name, --content, or --label.")
        raise typer.Exit(1)

    result = client.put(f"/api/prompts/{prompt_id}", json=body)
    display_result(result, get_output_format(), title="Prompt Updated")


@app.command()
def delete(
    prompt_id: Annotated[str, typer.Argument(help="Prompt UUID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a prompt."""
    if not force and not confirm_delete("prompt", prompt_id):
        raise typer.Abort()

    client = HecateClient(get_profile_name())
    client.delete(f"/api/prompts/{prompt_id}")
    typer.echo(f"Prompt {prompt_id} deleted.")


@app.command()
def versions(
    prompt_id: Annotated[str, typer.Argument(help="Prompt UUID")],
) -> None:
    """List versions of a prompt."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/prompts/{prompt_id}/versions")
    display_result(result, get_output_format(), title="Prompt Versions")


@app.command()
def by_label(
    label: Annotated[str, typer.Argument(help="Deployment label (e.g., production)")],
) -> None:
    """Get prompt by deployment label."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/prompts/by-label/{label}")
    display_result(result, get_output_format(), title=f"Prompt ({label})")
