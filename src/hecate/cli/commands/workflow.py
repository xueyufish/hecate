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


# --- 7.3 Workflow Evaluation -------------------------------------------------

eval_app = typer.Typer(no_args_is_help=True, help="Workflow evaluation commands")


@eval_app.command("run")
def eval_run(
    workflow_id: Annotated[str, typer.Option("--workflow-id", help="Workflow UUID")],
    dataset_id: Annotated[str, typer.Option("--dataset", help="Dataset UUID")],
    workflow_version: Annotated[
        int | None,
        typer.Option("--workflow-version", help="Pinned workflow version"),
    ] = None,
    repetitions: Annotated[
        int | None,
        typer.Option("--repetitions", help="Repetitions per item (default 1)"),
    ] = None,
    baseline_run_id: Annotated[
        str | None,
        typer.Option("--baseline-run-id", help="Baseline run id to compare against"),
    ] = None,
    evaluators: Annotated[
        str | None,
        typer.Option(
            "--evaluators",
            help="Comma-separated registered evaluator names",
        ),
    ] = None,
    threshold: Annotated[
        float | None,
        typer.Option("--threshold", help="Per-evaluator pass threshold"),
    ] = None,
    regression_threshold: Annotated[
        float | None,
        typer.Option("--regression-threshold", help="Regression delta fraction"),
    ] = None,
    poll_timeout: Annotated[
        int,
        typer.Option("--poll-timeout", help="Seconds to wait for run completion"),
    ] = 600,
    poll_interval: Annotated[
        int,
        typer.Option("--poll-interval", help="Seconds between status polls"),
    ] = 3,
) -> None:
    """Trigger a workflow evaluation run and stream its result.

    Exit codes: 0 = passed (no regression and no drift), 2 = dataset drift
    warning, 3 = regression detected. The CLI does NOT post PR comments;
    CI consumers render the JSON payload themselves.
    """
    client = HecateClient(get_profile_name())
    if not evaluators:
        typer.echo("Error: --evaluators is required (comma-separated names)", err=True)
        raise typer.Exit(2)

    evaluators_list = [name.strip() for name in evaluators.split(",") if name.strip()]
    version = workflow_version if workflow_version is not None else 0
    body: dict = {
        "workflow_id": workflow_id,
        "dataset_id": dataset_id,
        "evaluators": evaluators_list,
    }
    if threshold is not None:
        body["threshold"] = threshold
    if baseline_run_id:
        body["baseline_run_id"] = baseline_run_id
    if regression_threshold is not None:
        body["regression_threshold"] = regression_threshold
    if repetitions is not None:
        body["repetitions"] = repetitions

    trigger = client.post(
        f"/api/evaluation/workflow-evaluations/{version}/runs",
        json=body,
    )
    if not isinstance(trigger, dict) or "run_id" not in trigger:
        typer.echo(json_lib.dumps(trigger, indent=2))
        raise typer.Exit(3)
    run_id = trigger["run_id"]

    deadline = poll_timeout
    polled: dict = {}
    while deadline > 0:
        polled = client.get(f"/api/evaluation/runs/{run_id}")
        status = (polled.get("status") or "").lower() if isinstance(polled, dict) else ""
        if status in ("completed", "failed"):
            break
        deadline -= poll_interval
        import time as _time

        _time.sleep(poll_interval)
    else:
        polled = polled or {"status": "timeout", "run_id": run_id}

    candidate_id = polled.get("id") or run_id
    payload: dict = {"run_id": candidate_id, "raw": polled}

    dataset_drift = ((polled.get("summary") or {}).get("dataset_drift")) if isinstance(polled, dict) else None
    regressions = ((polled.get("summary") or {}).get("regressions")) if isinstance(polled, dict) else None
    has_regressions = bool(regressions)
    has_drift = bool(dataset_drift)
    payload["dataset_drift"] = dataset_drift
    payload["regressions"] = regressions or []
    payload["warnings"] = []
    payload["passed"] = (polled.get("status") == "completed") and not has_regressions

    if baseline_run_id:
        try:
            diff = client.post(
                "/api/evaluation/runs/compare",
                json={
                    "baseline_run_id": baseline_run_id,
                    "candidate_run_id": candidate_id,
                },
            )
        except Exception as exc:  # request failure → still emit the diff section
            diff = {"error": str(exc)}
        payload["comparison"] = diff
        if isinstance(diff, dict):
            if diff.get("overall_regressed"):
                has_regressions = True
            drift_raw = diff.get("dataset_drift")
            dataset_drift_compare = drift_raw if isinstance(drift_raw, dict) and drift_raw else {}
            if dataset_drift_compare:
                has_drift = True

    if has_drift:
        payload["warnings"].append(
            {
                "code": "dataset_drift",
                "message": "dataset snapshot hash differs from current dataset hash",
            }
        )
    if not payload["passed"]:
        payload["passed"] = False

    typer.echo(json_lib.dumps(payload, indent=2, default=str))

    if has_regressions:
        raise typer.Exit(3)
    if has_drift:
        raise typer.Exit(2)


app.add_typer(eval_app, name="eval")
