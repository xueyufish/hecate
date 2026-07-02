"""Message commands.

Provides:
- hecate message citations <message_id>
"""

from __future__ import annotations

from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import display_result

app = typer.Typer(no_args_is_help=True)


@app.command()
def citations(
    message_id: Annotated[str, typer.Argument(help="Message UUID")],
) -> None:
    """Get citations for a message."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/messages/{message_id}/citations")
    display_result(result, get_output_format(), title="Message Citations")
