"""Hecate interactive installer.

Drives the first-time setup after ``install.sh`` has cloned the repo and
verified prerequisites:

    1. Copy ``.env.example`` -> ``.env`` if missing.
    2. Prompt for an LLM provider key (or skip) if none is configured.
    3. Start docker compose infra (postgres / qdrant / minio / temporal).
    4. Apply alembic migrations.
    5. Run ``hecate preflight`` to surface any remaining issues.
    6. Print next steps; with ``--start-server``, also launch the Hecate
       container so the API is live at http://localhost:8000/docs.

Run via::

    uv run python install.py [--start-server]

Environment variables:

    HECATE_NONINTERACTIVE=1  skip interactive prompts (placeholders used)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()

REPO_ROOT = Path(__file__).resolve().parent
ENV_FILE = REPO_ROOT / ".env"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# (display name, env var, sample model id). Order = default choice order.
LLM_PROVIDERS = [
    ("OpenAI (gpt-4o, etc.)", "OPENAI_API_KEY", "gpt-4o"),
    ("Anthropic (Claude)", "ANTHROPIC_API_KEY", "anthropic/claude-3-5-sonnet-20241022"),
    ("Z.AI / Zhipu (GLM)", "ZAI_API_KEY", "zai/glm-4.7-flash"),
    ("DeepSeek", "DEEPSEEK_API_KEY", "deepseek/deepseek-chat"),
    ("Alibaba Qwen (DashScope)", "DASHSCOPE_API_KEY", "dashscope/qwen-turbo"),
    ("Ollama (local, no key)", "OLLAMA_API_KEY", "ollama/llama3.1"),
]

DOCKER_SERVICES = ("postgres", "qdrant", "minio", "temporal")


def _docker_env() -> dict[str, str]:
    """Return os.environ with macOS Docker Desktop bin dir prepended to PATH.

    macOS Docker Desktop ships ``docker-credential-desktop`` (and other
    helper binaries) under
    ``/Applications/Docker.app/Contents/Resources/bin``. The GUI shells
    that Docker Desktop launches add this directory to ``PATH``, but
    terminal sessions inherit only the system PATH — so ``docker pull``
    fails with::

        error getting credentials - err: exec: "docker-credential-desktop":
        executable file not found in $PATH

    Prepending the directory for every docker subprocess invocation makes
    the installer resilient regardless of how the user opened the shell.
    Linux distros ship credential helpers via the ``docker-ce`` package
    and already have them on PATH, so this is a no-op there.
    """
    env = os.environ.copy()
    if sys.platform == "darwin":
        docker_bin = Path("/Applications/Docker.app/Contents/Resources/bin")
        if docker_bin.is_dir():
            env["PATH"] = f"{docker_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Hecate first-time installer.",
    )
    parser.add_argument(
        "--start-server",
        action="store_true",
        help=(
            "After preflight passes, launch the Hecate container so the API "
            "is live at http://localhost:8000/docs. Off by default; the "
            "installer otherwise stops after validating the environment."
        ),
    )
    return parser.parse_args()


def header(title: str) -> None:
    """Print a section header in a styled panel."""
    console.print(Panel(title, style="bold cyan"))


def read_env_value(env_text: str, key: str) -> str | None:
    """Return the current value for ``key`` in ``env_text``, ignoring comments."""
    for line in env_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, v = stripped.partition("=")
        if k.strip() == key:
            value = v.strip().strip('"').strip("'")
            return value or None
    return None


def write_env_value(env_text: str, key: str, value: str) -> str:
    """Set ``key=value`` in ``env_text``, replacing in place or appending."""
    lines = env_text.splitlines()
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, _, _ = stripped.partition("=")
        if k.strip() == key:
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        if env_text and not env_text.endswith("\n"):
            lines.append("")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def ensure_env_file() -> None:
    """Copy .env.example -> .env when .env is missing."""
    if not ENV_EXAMPLE.exists():
        console.print(f"[red]ERROR: {ENV_EXAMPLE} not found[/red]")
        sys.exit(1)
    if not ENV_FILE.exists():
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        console.print(f"[green]Created {ENV_FILE.name} from template[/green]")


def prompt_for_llm_key() -> None:
    """Interactive prompt for an LLM provider key when none is set."""
    text = ENV_FILE.read_text(encoding="utf-8")
    detected = [(env_var, name) for name, env_var, _ in LLM_PROVIDERS if read_env_value(text, env_var)]
    if detected:
        console.print(f"[green].env already has: {', '.join(v for v, _ in detected)} — skipping prompt[/green]")
        return

    if os.environ.get("HECATE_NONINTERACTIVE"):
        console.print(
            "[yellow]HECATE_NONINTERACTIVE=1 and no key set; edit .env before sending chat requests.[/yellow]"
        )
        return

    console.print("\n[bold]Hecate routes chat through LiteLLM. Pick a provider to configure:[/bold]")
    for i, (name, env_var, _sample) in enumerate(LLM_PROVIDERS, 1):
        console.print(f"  [cyan]{i}[/cyan]. {name}  [dim]({env_var})[/dim]")
    console.print("  [cyan]0[/cyan]. Skip — I'll edit .env myself")

    raw = Prompt.ask(
        "Choose a provider",
        choices=[str(i) for i in range(len(LLM_PROVIDERS) + 1)],
        default="3",
    )
    idx = int(raw)
    if idx == 0:
        console.print("[yellow]Skipped — set a provider key in .env before sending chat requests.[/yellow]")
        return

    name, env_var, _sample = LLM_PROVIDERS[idx - 1]
    if env_var == "OLLAMA_API_KEY":
        console.print("[cyan]Ollama needs no key; make sure `ollama serve` runs on localhost:11434.[/cyan]")
        value = "ollama"
    else:
        value = Prompt.ask(f"Paste your {env_var}", password=True)

    new_text = write_env_value(text, env_var, value)
    ENV_FILE.write_text(new_text, encoding="utf-8")
    console.print(f"[green]{env_var} saved to .env[/green]")


def docker_daemon_reachable() -> bool:
    """Return True if `docker info` exits 0 (daemon is up)."""
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(  # noqa: S603
            ["docker", "info"],  # noqa: S607
            check=True,
            capture_output=True,
            timeout=5,
            env=_docker_env(),
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def start_infra() -> None:
    """Bring up postgres / qdrant / minio / temporal via docker compose."""
    if not docker_daemon_reachable():
        console.print("[yellow]Docker daemon not reachable — skipping infra start.[/yellow]")
        console.print("  Install Docker, then run:")
        compose_cmd = f"  docker compose -f {REPO_ROOT / 'docker' / 'docker-compose.yml'} up -d " + " ".join(
            DOCKER_SERVICES
        )
        console.print(compose_cmd)
        return
    header("Starting infrastructure (postgres / qdrant / minio / temporal)")
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "compose",
            "-f",
            str(REPO_ROOT / "docker" / "docker-compose.yml"),
            "up",
            "-d",
            *DOCKER_SERVICES,
        ],
        cwd=REPO_ROOT,
        check=True,
        env=_docker_env(),
    )


def run_migrations() -> None:
    """Apply alembic migrations against the running database."""
    header("Applying database migrations")
    subprocess.run(  # noqa: S603
        ["uv", "run", "alembic", "upgrade", "head"],  # noqa: S607
        cwd=REPO_ROOT,
        check=True,
    )


def run_preflight() -> None:
    """Run ``hecate preflight`` to surface remaining setup issues."""
    header("Running hecate preflight")
    result = subprocess.run(  # noqa: S603
        ["uv", "run", "hecate", "preflight"],  # noqa: S607
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        console.print("[red]hecate preflight failed — see output above.[/red]")


def start_server() -> None:
    """Bring up the Hecate container (uvicorn + chat API at port 8000)."""
    if not docker_daemon_reachable():
        console.print("[yellow]Docker daemon not reachable — skipping Hecate container start.[/yellow]")
        console.print("  Start it manually with:")
        console.print(f"  docker compose -f {REPO_ROOT / 'docker' / 'docker-compose.yml'} up -d hecate")
        return
    header("Starting Hecate container")
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "docker",
            "compose",
            "-f",
            str(REPO_ROOT / "docker" / "docker-compose.yml"),
            "up",
            "-d",
            "hecate",
        ],
        cwd=REPO_ROOT,
        check=True,
        env=_docker_env(),
    )


def main() -> int:
    args = _parse_args()
    # fastmcp 4.x is currently beta-only; allow pre-releases for uv resolution.
    os.environ.setdefault("UV_PRERELEASE", "allow")
    header("Hecate installer")
    console.print(f"Repository: [cyan]{REPO_ROOT}[/cyan]")
    ensure_env_file()
    prompt_for_llm_key()
    start_infra()
    run_migrations()
    run_preflight()
    if args.start_server:
        start_server()
        console.print("\n[bold green]Hecate is running.[/bold green]")
        console.print("Open:")
        console.print("  [cyan]http://localhost:8000/docs[/cyan]            (Swagger UI)")
        console.print("  [cyan]http://localhost:8000/health/ready[/cyan]   (readiness probe)")
        console.print("Stop with: [cyan]docker compose -f docker/docker-compose.yml stop hecate[/cyan]")
    else:
        console.print("\n[bold green]Hecate is ready.[/bold green]")
        console.print("Start the server with one of:")
        console.print("  [cyan]uv run uvicorn hecate.main:app --reload[/cyan]                           (dev mode)")
        console.print("  [cyan]docker compose -f docker/docker-compose.yml up -d hecate[/cyan]    (container)")
        console.print("Or rerun this installer with [cyan]--start-server[/cyan] to launch the container automatically.")
        console.print("Then open:")
        console.print("  [cyan]http://localhost:8000/docs[/cyan]            (Swagger UI)")
        console.print("  [cyan]http://localhost:8000/health/ready[/cyan]   (readiness probe)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
