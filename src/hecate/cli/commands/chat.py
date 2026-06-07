"""Chat commands with streaming support.

Provides:
- hecate chat send <agent_id> <message>  — one-shot
- hecate chat interactive <agent_id>     — REPL with streaming
"""

from __future__ import annotations

import json
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel

from hecate.cli.client import HecateClient
from hecate.cli.config import get_output_format, get_profile_name
from hecate.cli.output import display_result

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command()
def send(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
    message: Annotated[str, typer.Argument(help="Message to send")],
    session_id: Annotated[str | None, typer.Option("--session-id", help="Session UUID for continuity")] = None,
) -> None:
    """Send a single message to an agent (non-streaming)."""
    client = HecateClient(get_profile_name())

    body: dict = {
        "model": agent_id,
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    if session_id:
        body["session_id"] = session_id

    result = client.post("/v1/chat/completions", json=body)
    if result and "choices" in result:
        content = result["choices"][0].get("message", {}).get("content", "")
        console.print(Panel(content, title="Agent Response"))
    else:
        display_result(result, get_output_format())


@app.command(name="interactive")
def interactive_chat(
    agent_id: Annotated[str, typer.Argument(help="Agent UUID")],
    session_id: Annotated[str | None, typer.Option("--session-id", help="Resume existing session")] = None,
) -> None:
    """Start an interactive chat session with streaming."""
    client = HecateClient(get_profile_name())
    history: list[dict] = []

    console.print(f"[bold green]Hecate Chat[/bold green] — Agent: {agent_id}")
    console.print("[dim]Type your message, or /clear, /history, /exit[/dim]")
    console.print()

    while True:
        try:
            user_input = console.input("[bold blue]>[/bold blue] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/dim]")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # Slash commands
        if user_input == "/exit":
            console.print("[dim]Goodbye![/dim]")
            break
        if user_input == "/clear":
            history.clear()
            console.print("[dim]Context cleared.[/dim]")
            continue
        if user_input == "/history":
            if not history:
                console.print("[dim]No messages yet.[/dim]")
            else:
                for msg in history:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if role == "user":
                        console.print(f"[blue]You:[/blue] {content}")
                    else:
                        console.print(f"[green]Agent:[/green] {content}")
            continue

        # Build messages
        history.append({"role": "user", "content": user_input})
        body: dict = {
            "model": agent_id,
            "messages": list(history),
            "stream": True,
        }
        if session_id:
            body["session_id"] = session_id

        # Streaming response
        console.print("[bold green]Agent:[/bold green] ", end="")
        full_content = ""
        last_data: dict = {}
        try:
            for data_str in client.stream_request("POST", "/v1/chat/completions", json=body):
                try:
                    last_data = json.loads(data_str)
                    delta = last_data.get("choices", [{}])[0].get("delta", {})
                    token = delta.get("content", "")
                    if token:
                        console.print(token, end="", highlight=False)
                        full_content += token
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            console.print(f"\n[red]Stream error:[/red] {e}")
            continue

        console.print()  # newline after response
        history.append({"role": "assistant", "content": full_content})

        # Capture session_id from response metadata for continuity
        if "session_id" in last_data:
            session_id = last_data["session_id"]
