"""Template commands.

Provides:
- hecate template agents                              — list agent templates
- hecate template agents instantiate <id>             — instantiate agent template
- hecate template orchestration                       — list orchestration templates
"""

from __future__ import annotations

from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import display_result

app = typer.Typer(no_args_is_help=True)

# Sub-groups
agents_app = typer.Typer(no_args_is_help=True)
app.add_typer(agents_app, name="agents", help="Agent templates")


@agents_app.command()
def list() -> None:  # noqa: A001
    """List available agent templates."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/agent-templates")
    display_result(result, get_output_format(), title="Agent Templates")


@agents_app.command()
def instantiate(
    template_id: Annotated[str, typer.Argument(help="Template ID")],
    name: Annotated[str | None, typer.Option("--name", "-n", help="Agent name")] = None,
) -> None:
    """Instantiate an agent from a template."""
    client = HecateClient(get_profile_name())
    body: dict = {}
    if name:
        body["name"] = name
    result = client.post(f"/api/agent-templates/{template_id}/instantiate", json=body)
    display_result(result, get_output_format(), title="Agent Created from Template")


@app.command()
def orchestration() -> None:
    """List available orchestration templates."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/orchestration-templates")
    display_result(result, get_output_format(), title="Orchestration Templates")
