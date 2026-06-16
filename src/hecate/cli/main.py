"""Root CLI application.

Registers all subcommand groups and provides global options.
"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.console import Console

from hecate.cli.config import set_global_state

console = Console()

app = typer.Typer(
    name="hecate",
    help="Hecate Agent Platform CLI — manage agents, sessions, knowledge bases, and more.",
    no_args_is_help=True,
)

# Global options stored in typer context
profile_opt = Annotated[
    str | None,
    typer.Option("--profile", "-p", help="Configuration profile name"),
]
json_opt = Annotated[
    bool,
    typer.Option("--json", "-j", help="Output in JSON format"),
]


@app.callback()
def main(
    profile: profile_opt = None,
    json_output: json_opt = False,
) -> None:
    """Hecate Agent Platform CLI."""
    set_global_state(profile or "default", json_output)


# --- Version ---
@app.command()
def version() -> None:
    """Show Hecate CLI version."""
    console.print("hecate-cli 0.1.0")


# --- Register subcommand groups ---
# Each module creates its own typer.Typer app which gets added here


def _register_commands() -> None:
    """Register all CLI subcommand groups."""
    # Lazy imports to avoid circular dependencies and speed up --help
    from hecate.cli.commands.agent import app as agent_app
    from hecate.cli.commands.auth import app as auth_app
    from hecate.cli.commands.chat import app as chat_app
    from hecate.cli.commands.config_cmd import app as config_app
    from hecate.cli.commands.conversation import app as conversation_app
    from hecate.cli.commands.kb import app as kb_app
    from hecate.cli.commands.memory import app as memory_app
    from hecate.cli.commands.message import app as message_app
    from hecate.cli.commands.model import app as model_app
    from hecate.cli.commands.prompt import app as prompt_app
    from hecate.cli.commands.session import app as session_app
    from hecate.cli.commands.skill import app as skill_app
    from hecate.cli.commands.template import app as template_app
    from hecate.cli.commands.tool import app as tool_app
    from hecate.cli.commands.workflow import app as workflow_app

    app.add_typer(config_app, name="config", help="Manage CLI configuration")
    app.add_typer(auth_app, name="auth", help="Authentication")
    app.add_typer(agent_app, name="agent", help="Agent CRUD operations")
    app.add_typer(session_app, name="session", help="Session management")
    app.add_typer(chat_app, name="chat", help="Chat with agents")
    app.add_typer(kb_app, name="kb", help="Knowledge base operations")
    app.add_typer(tool_app, name="tool", help="Tool operations")
    app.add_typer(skill_app, name="skill", help="Skill CRUD operations")
    app.add_typer(workflow_app, name="workflow", help="Workflow CRUD and execution")
    app.add_typer(prompt_app, name="prompt", help="Prompt CRUD and versioning")
    app.add_typer(memory_app, name="memory", help="Memory management")
    app.add_typer(template_app, name="template", help="Agent/orchestration templates")
    app.add_typer(conversation_app, name="conversation", help="Conversation management")
    app.add_typer(model_app, name="model", help="Model and provider management")
    app.add_typer(message_app, name="message", help="Message operations")


# Register on module load
_register_commands()
