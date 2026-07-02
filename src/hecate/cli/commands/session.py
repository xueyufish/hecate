"""Session management commands.

Provides:
- hecate session create
- hecate session list
- hecate session get <id>
- hecate session resume <id>
"""

from __future__ import annotations

from typing import Annotated

import typer

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import display_result

app = typer.Typer(no_args_is_help=True)


@app.command()
def create(
    agent_id: Annotated[str, typer.Option("--agent-id", "-a", help="Agent UUID")],
    conversation_id: Annotated[str | None, typer.Option("--conversation-id", help="Conversation UUID")] = None,
) -> None:
    """Create a new session for an agent."""
    client = HecateClient(get_profile_name())

    body: dict = {"agent_id": agent_id}
    if conversation_id:
        body["conversation_id"] = conversation_id

    result = client.post("/api/sessions", json=body)
    display_result(result, get_output_format(), title="Session Created")


@app.command()
def list(
    page: Annotated[int, typer.Option("--page", help="Page number")] = 1,
    page_size: Annotated[int, typer.Option("--page-size", help="Items per page")] = 20,
) -> None:
    """List all sessions."""
    client = HecateClient(get_profile_name())
    result = client.get("/api/sessions", params={"page": page, "page_size": page_size})
    display_result(result, get_output_format(), columns=["id", "agent_id", "status", "created_at"], title="Sessions")


@app.command()
def get(
    session_id: Annotated[str, typer.Argument(help="Session UUID")],
) -> None:
    """Get session details."""
    client = HecateClient(get_profile_name())
    result = client.get(f"/api/sessions/{session_id}")
    display_result(result, get_output_format(), title="Session Details")


@app.command()
def resume(
    session_id: Annotated[str, typer.Argument(help="Session UUID")],
    message: Annotated[str, typer.Option("--message", "-m", help="Resume value/message")],
) -> None:
    """Resume an interrupted session."""
    client = HecateClient(get_profile_name())
    result = client.post(f"/api/sessions/{session_id}/resume", json={"resume_value": message})
    display_result(result, get_output_format(), title="Session Resumed")
