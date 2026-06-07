"""Skill management commands.

Provides:
- hecate skill list
- hecate skill create
- hecate skill get <id>
- hecate skill update <id>
- hecate skill delete <id>
- hecate skill import <file>
"""

from __future__ import annotations

from pathlib import Path
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
    """List all skills."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/skills", params={"page": page, "page_size": page_size})
    display_result(result, get_output_format(), columns=["id", "name", "source", "created_at"], title="Skills")


@app.command()
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Skill name (lowercase-hyphenated)")],
    content: Annotated[str, typer.Option("--content", "-c", help="SKILL.md content")],
    source: Annotated[str, typer.Option("--source", "-s", help="Source: system or user")] = "user",
) -> None:
    """Create a new skill."""
    client = HecateClient(get_profile_name())
    body = {"name": name, "content": content, "source": source}
    result = client.post("/api/skills", json=body)
    display_result(result, get_output_format(), title="Skill Created")


@app.command()
def get(
    skill_id: Annotated[str, typer.Argument(help="Skill UUID")],
) -> None:
    """Get skill details."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/skills/{skill_id}")
    display_result(result, get_output_format(), title="Skill Details")


@app.command()
def update(
    skill_id: Annotated[str, typer.Argument(help="Skill UUID")],
    name: Annotated[str | None, typer.Option("--name", "-n", help="New name")] = None,
    content: Annotated[str | None, typer.Option("--content", "-c", help="New content")] = None,
) -> None:
    """Update an existing skill."""
    client = HecateClient(get_profile_name())
    body: dict = {}
    if name:
        body["name"] = name
    if content:
        body["content"] = content

    if not body:
        typer.echo("No fields to update. Use --name or --content.")
        raise typer.Exit(1)

    result = client.put(f"/api/skills/{skill_id}", json=body)
    display_result(result, get_output_format(), title="Skill Updated")


@app.command()
def delete(
    skill_id: Annotated[str, typer.Argument(help="Skill UUID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a skill."""
    if not force and not confirm_delete("skill", skill_id):
        raise typer.Abort()

    client = HecateClient(get_profile_name())
    client.delete(f"/api/skills/{skill_id}")
    typer.echo(f"Skill {skill_id} deleted.")


@app.command()
def import_skill(
    file_path: Annotated[str, typer.Argument(help="Path to SKILL.md file")],
) -> None:
    """Import a skill from a SKILL.md file."""
    path = Path(file_path)
    if not path.exists():
        typer.echo(f"Error: File not found: {file_path}")
        raise typer.Exit(1)

    client = HecateClient(get_profile_name())

    with open(path, "rb") as f:
        result = client.post(
            "/api/skills/import",
            files={"file": (path.name, f, "text/markdown")},
            content_type="multipart/form-data",
        )

    display_result(result, get_output_format(), title="Skill Imported")
