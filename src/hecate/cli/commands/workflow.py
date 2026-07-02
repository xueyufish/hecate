"""Workflow management commands.

Provides:
- hecate workflow list
- hecate workflow create
- hecate workflow get <id>
- hecate workflow update <id>
- hecate workflow delete <id>
- hecate workflow validate <id>
- hecate workflow test-run <id>
- hecate workflow versions <id>
- hecate workflow runs <id>
"""

from __future__ import annotations

import json as json_lib
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
    """List all workflows."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/workflows", params={"page": page, "page_size": page_size})
    display_result(result, get_output_format(), columns=["id", "name", "created_at"], title="Workflows")


@app.command()
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Workflow name")],
    graph_dsl: Annotated[
        str | None,
        typer.Option("--graph-dsl", "-g", help="Graph DSL JSON string or @file.json"),
    ] = None,
) -> None:
    """Create a new workflow."""
    client = HecateClient(get_profile_name())

    body: dict = {"name": name}
    if graph_dsl:
        if graph_dsl.startswith("@"):
            file_path = graph_dsl[1:]
            path = Path(file_path)
            if not path.exists():
                typer.echo(f"Error: File not found: {file_path}")
                raise typer.Exit(1)
            body["graph_dsl"] = json_lib.loads(path.read_text())
        else:
            body["graph_dsl"] = json_lib.loads(graph_dsl)

    result = client.post("/api/workflows", json=body)
    display_result(result, get_output_format(), title="Workflow Created")


@app.command()
def get(
    workflow_id: Annotated[str, typer.Argument(help="Workflow UUID")],
) -> None:
    """Get workflow details."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/workflows/{workflow_id}")
    display_result(result, get_output_format(), title="Workflow Details")


@app.command()
def update(
    workflow_id: Annotated[str, typer.Argument(help="Workflow UUID")],
    name: Annotated[str | None, typer.Option("--name", "-n", help="New name")] = None,
    graph_dsl: Annotated[str | None, typer.Option("--graph-dsl", "-g", help="Graph DSL JSON or @file.json")] = None,
) -> None:
    """Update an existing workflow."""
    client = HecateClient(get_profile_name())
    body: dict = {}

    if name:
        body["name"] = name
    if graph_dsl:
        if graph_dsl.startswith("@"):
            file_path = graph_dsl[1:]
            path = Path(file_path)
            if not path.exists():
                typer.echo(f"Error: File not found: {file_path}")
                raise typer.Exit(1)
            body["graph_dsl"] = json_lib.loads(path.read_text())
        else:
            body["graph_dsl"] = json_lib.loads(graph_dsl)

    if not body:
        typer.echo("No fields to update. Use --name or --graph-dsl.")
        raise typer.Exit(1)

    result = client.put(f"/api/workflows/{workflow_id}", json=body)
    display_result(result, get_output_format(), title="Workflow Updated")


@app.command()
def delete(
    workflow_id: Annotated[str, typer.Argument(help="Workflow UUID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a workflow."""
    if not force and not confirm_delete("workflow", workflow_id):
        raise typer.Abort()

    client = HecateClient(get_profile_name())
    client.delete(f"/api/workflows/{workflow_id}")
    typer.echo(f"Workflow {workflow_id} deleted.")


@app.command()
def validate(
    workflow_id: Annotated[str, typer.Argument(help="Workflow UUID")],
) -> None:
    """Validate a workflow's graph DSL."""
    client = HecateClient(get_profile_name())
    result = client.post(f"/api/workflows/{workflow_id}/validate")
    display_result(result, get_output_format(), title="Validation Result")


@app.command()
def test_run(
    workflow_id: Annotated[str, typer.Argument(help="Workflow UUID")],
    input_data: Annotated[str | None, typer.Option("--input", "-i", help="Input JSON or @file.json")] = None,
) -> None:
    """Execute a test run of a workflow."""
    client = HecateClient(get_profile_name())

    body: dict = {}
    if input_data:
        if input_data.startswith("@"):
            file_path = input_data[1:]
            body = json_lib.loads(Path(file_path).read_text())
        else:
            body = json_lib.loads(input_data)

    result = client.post(f"/api/workflows/{workflow_id}/test-run", json=body)
    display_result(result, get_output_format(), title="Test Run Result")


@app.command()
def versions(
    workflow_id: Annotated[str, typer.Argument(help="Workflow UUID")],
) -> None:
    """List versions of a workflow."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/workflows/{workflow_id}/versions")
    display_result(result, get_output_format(), title="Workflow Versions")


@app.command()
def runs(
    workflow_id: Annotated[str, typer.Argument(help="Workflow UUID")],
) -> None:
    """List test run history for a workflow."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/workflows/{workflow_id}/runs")
    display_result(result, get_output_format(), title="Workflow Runs")
