"""Authentication commands.

Provides:
- hecate auth login --email <email>
- hecate auth whoami
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name, set_profile_value
from hecate.cli.output import display_result

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command()
def login(
    email: Annotated[str, typer.Option("--email", "-e", help="Login email")],
    password: Annotated[str, typer.Option("--password", "-p", help="Password (will prompt if omitted)")] = "",
) -> None:
    """Login with email and password to obtain JWT tokens."""
    if not password:
        from rich.prompt import Prompt

        password = Prompt.ask("Password", password=True)

    client = HecateClient(get_profile_name())
    result = client.post("/api/auth/login", json={"email": email, "password": password})

    if result:
        access_token = result.get("access_token", "")
        refresh_token = result.get("refresh_token", "")

        profile_name = get_profile_name()
        if access_token:
            set_profile_value(profile_name, "access_token", access_token)
        if refresh_token:
            set_profile_value(profile_name, "refresh_token", refresh_token)

        console.print(f"[green]Logged in as {email}[/green]")
    else:
        console.print("[red]Login failed[/red]")
        raise typer.Exit(1)


@app.command()
def whoami() -> None:
    """Display the current authenticated user."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/auth/me")
    display_result(result, get_output_format(), title="Current User")
