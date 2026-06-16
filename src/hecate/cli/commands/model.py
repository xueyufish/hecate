"""Model and provider management commands.

Provides:
- hecate model list                     — list available models
- hecate model providers list           — list model providers
- hecate model providers create         — create provider
- hecate model providers test <id>      — test provider connectivity
"""

from __future__ import annotations

from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import confirm_delete, display_result

app = typer.Typer(no_args_is_help=True)

# Sub-group for providers
providers_app = typer.Typer(no_args_is_help=True)
app.add_typer(providers_app, name="providers", help="Model provider management")


@app.command()
def list(
    page: Annotated[int, typer.Option("--page", help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Items per page")] = 20,
) -> None:
    """List available models."""
    client = HecateClient(get_profile_name())
    result = client.get("/v1/models")
    display_result(result, get_output_format(), columns=["id", "provider", "provider_display_name"], title="Models")


@providers_app.command(name="list")
def list_providers() -> None:
    """List model providers."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/model-providers")
    display_result(
        result,
        get_output_format(),
        columns=["id", "name", "provider_type", "status"],
        title="Model Providers",
    )


@providers_app.command()
def create(
    name: Annotated[str, typer.Option("--name", "-n", help="Provider name")],
    provider_type: Annotated[str, typer.Option("--type", "-t", help="Provider type (e.g., openai, anthropic)")],
    api_key: Annotated[str, typer.Option("--api-key", help="API key for the provider")],
    base_url: Annotated[str | None, typer.Option("--base-url", help="Custom base URL")] = None,
) -> None:
    """Create a model provider."""
    client = HecateClient(get_profile_name())
    body: dict = {
        "name": name,
        "provider_type": provider_type,
        "api_key": api_key,
    }
    if base_url:
        body["base_url"] = base_url
    result = client.post("/api/model-providers", json=body)
    display_result(result, get_output_format(), title="Provider Created")


@providers_app.command()
def test(
    provider_id: Annotated[str, typer.Argument(help="Provider UUID")],
) -> None:
    """Test provider connectivity."""
    client = HecateClient(get_profile_name())
    result = client.post(f"/api/model-providers/{provider_id}/test")
    display_result(result, get_output_format(), title="Connectivity Test Result")


@providers_app.command()
def delete(
    provider_id: Annotated[str, typer.Argument(help="Provider UUID")],
    force: Annotated[bool, typer.Option("--force", "-f", help="Skip confirmation")] = False,
) -> None:
    """Delete a model provider."""
    if not force and not confirm_delete("provider", provider_id):
        raise typer.Abort()

    client = HecateClient(get_profile_name())
    client.delete(f"/api/model-providers/{provider_id}")
    typer.echo(f"Provider {provider_id} deleted.")
