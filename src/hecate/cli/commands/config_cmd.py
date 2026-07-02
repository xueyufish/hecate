"""CLI configuration commands.

Provides:
- hecate config set <key> <value>
- hecate config get <key>
- hecate config show
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from hecate.cli.config import get_profile, get_profile_name, mask_value, set_profile_value

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command()
def set(
    key: Annotated[str, typer.Argument(help="Configuration key (e.g., api_key, base_url)")],
    value: Annotated[str, typer.Argument(help="Value to set")],
) -> None:
    """Set a configuration value for the active profile."""
    profile_name = get_profile_name()
    set_profile_value(profile_name, key, value)
    console.print(f"[green]Set[/green] {key} in profile '{profile_name}'")


@app.command()
def get(
    key: Annotated[str, typer.Argument(help="Configuration key to display")],
) -> None:
    """Display a single configuration value."""
    profile_name = get_profile_name()
    profile = get_profile(profile_name)
    value = profile.get(key, "")
    display_value = mask_value(key, str(value))
    console.print(f"{key} = {display_value}")


@app.command(name="show")
def show_config() -> None:
    """Display all configuration values (secrets masked)."""
    profile_name = get_profile_name()
    profile = get_profile(profile_name)

    console.print(f"[bold]Profile:[/bold] {profile_name}")
    console.print()
    for key, value in profile.items():
        display_value = mask_value(key, str(value))
        console.print(f"  {key} = {display_value}")


@app.command()
def profiles() -> None:
    """List all configured profiles."""
    from hecate.cli.config import load_config

    config = load_config()
    all_profiles = config.get("profiles", {})

    if not all_profiles:
        console.print("[dim]No profiles configured. Run 'hecate config set api_key <key>' to get started.[/dim]")
        return

    from hecate.cli.output import format_table

    items = []
    for name, data in all_profiles.items():
        items.append(
            {
                "name": name,
                "base_url": data.get("base_url", ""),
                "api_key": mask_value("api_key", data.get("api_key", "")),
            }
        )

    format_table(items, ["name", "base_url", "api_key"], title="Profiles")
