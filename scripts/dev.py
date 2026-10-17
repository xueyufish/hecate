"""Shared development environment, checks, and worktree-aware Git hooks.

Run with Python directly, or use scripts/dev.sh to bootstrap through uv.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12"
WORKSPACE_PACKAGES = (
    "hecate",
    "hecate-enterprise",
    "hecate-memory",
    "hecate-ops",
    "hecate-llm",
    "hecate-sandbox",
    "hecate-channel-slack",
    "hecate-channel-feishu",
    "hecate-runner",
)
DEVELOPMENT_TOOLS = ("pytest", "ruff", "mypy", "commitizen")
CHECK_TARGETS = (
    "src/hecate/",
    "packages/",
    "tests/",
    "scripts/feature_inventory.py",
    "scripts/dev.py",
)
CHECKS = {
    "lint": ("ruff", "check", *CHECK_TARGETS),
    "format": ("ruff", "format", "--check", *CHECK_TARGETS),
    "types": ("mypy", "src/", "packages/", "scripts/dev.py"),
}
HOOK_NAMES = ("pre-commit", "commit-msg", "pre-push")
HOOK_MARKER = "# Hecate managed hook: dispatch-v1"


class DevError(RuntimeError):
    """A development prerequisite or operation could not be satisfied."""


def _environment() -> dict[str, str]:
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(ROOT / ".venv")
    env["VIRTUAL_ENV"] = str(ROOT / ".venv")
    return env


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    if not capture:
        print(f"dev: {shlex.join(command)}", flush=True)
    return subprocess.run(command, cwd=ROOT, env=_environment(), text=True, capture_output=capture, check=False)


def _python() -> Path:
    for relative in (".venv/bin/python", ".venv/Scripts/python.exe"):
        candidate = ROOT / relative
        if candidate.is_file():
            return candidate
    raise DevError("Missing worktree interpreter. Run: bash scripts/dev.sh sync")


def sync_environment(*, check: bool = False, profile: str = "dev") -> int:
    """Synchronize the locked development workspace, or check without syncing."""
    uv = shutil.which("uv")
    if uv is None:
        raise DevError("uv is required. Install uv, then run: bash scripts/dev.sh sync")
    command = [uv, "sync", "--locked", "--python", PYTHON_VERSION]
    if profile == "lint":
        command.extend(("--only-group", "lint"))
    else:
        command.extend(("--extra", "dev"))
        if profile == "postgres":
            command.extend(("--extra", "postgres"))
        for package in WORKSPACE_PACKAGES:
            command.extend(("--package", package))
    if check:
        command.append("--check")
    return _run(command).returncode


def run_check(name: str) -> int:
    """Run shared check definitions and stop at the first failing check."""
    python = str(_python())
    for selected in CHECKS if name == "all" else (name,):
        result = _run([python, "-m", *CHECKS[selected]])
        if result.returncode:
            return result.returncode
    return 0


def run_tests(arguments: list[str]) -> int:
    """Forward an explicit pytest invocation without selecting more tests."""
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments:
        raise DevError("Specify pytest targets/options, for example: test tests/test_scripts/ -q")
    return _run([str(_python()), "-m", "pytest", *arguments]).returncode


def _git(*arguments: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise DevError("git is required for hook operations")
    result = _run([git, *arguments], capture=True)
    if result.returncode:
        raise DevError(result.stderr.strip() or "Git hook directory could not be resolved")
    return result.stdout.strip()


def _hooks_directory() -> Path:
    location = Path(_git("rev-parse", "--git-path", "hooks"))
    return location if location.is_absolute() else ROOT / location


def hook_content(name: str) -> str:
    """Return a dispatcher that resolves the invoking worktree at hook time."""
    if name not in HOOK_NAMES:
        raise DevError(f"Unsupported hook: {name}")
    return (
        "#!/usr/bin/env bash\n"
        f"{HOOK_MARKER}\n"
        "set -euo pipefail\n"
        'root="$(git rev-parse --show-toplevel)"\n'
        f'exec bash "$root/scripts/{name}.sh" "$@"\n'
    )


def _hook_matches(path: Path, name: str) -> bool:
    if not path.is_file() or path.read_bytes() != hook_content(name).encode("utf-8"):
        return False
    return os.name == "nt" or bool(path.stat().st_mode & stat.S_IXUSR)


def install_hooks(*, replace: bool = False) -> int:
    """Install dispatchers, preserving explicitly replaced hooks for recovery."""
    directory = _hooks_directory()
    backups: list[tuple[Path, Path]] = []
    for name in HOOK_NAMES:
        path = directory / name
        if not path.exists() or _hook_matches(path, name):
            continue
        if not path.is_file() or path.is_symlink():
            raise DevError(f"Refusing to replace non-regular hook: {path}")
        if HOOK_MARKER.encode("utf-8") in path.read_bytes():
            continue
        if not replace:
            raise DevError(f"Existing hook: {path}. Use hooks install --replace to preserve and replace it.")
        backup = path.with_name(f"{name}.before-hecate")
        if backup.exists() and backup.read_bytes() != path.read_bytes():
            raise DevError(f"Backup already exists with different content: {backup}")
        backups.append((path, backup))
    directory.mkdir(parents=True, exist_ok=True)
    for path, backup in backups:
        if not backup.exists():
            shutil.copy2(path, backup)
            print(f"dev: preserved {backup}")
    for name in HOOK_NAMES:
        path = directory / name
        if _hook_matches(path, name):
            continue
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.hecate-", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(hook_content(name))
            temporary.chmod(0o755)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"dev: managed hooks installed in {directory}")
    return 0


def check_hooks() -> int:
    """Check that Git will execute the current managed hook dispatchers."""
    directory = _hooks_directory()
    missing = [name for name in HOOK_NAMES if not _hook_matches(directory / name, name)]
    if missing:
        raise DevError(f"Unmanaged/missing hooks: {', '.join(missing)}. Run: bash scripts/dev.sh hooks install")
    print(f"dev: managed hooks ready in {directory}")
    return 0


def doctor() -> int:
    """Report development readiness without installing or modifying anything."""
    failures: list[str] = []
    if shutil.which("uv") is None:
        failures.append("uv is missing")
    try:
        python = _python()
        probe = (
            "import importlib.metadata as m,json,sys; "
            "names=json.loads(sys.argv[1]); "
            "installed={d.metadata['Name'].lower().replace('_','-') for d in m.distributions()}; "
            "print(json.dumps({'python':list(sys.version_info[:2]),"
            "'missing':[n for n in names if n not in installed]}))"
        )
        result = _run(
            [str(python), "-I", "-c", probe, json.dumps([*WORKSPACE_PACKAGES, *DEVELOPMENT_TOOLS])], capture=True
        )
        if result.returncode:
            failures.append("Worktree interpreter could not inspect installed packages")
        else:
            status = json.loads(result.stdout)
            if status["python"] != [3, 12]:
                failures.append(f"Expected Python {PYTHON_VERSION}; found {status['python']}")
            if status["missing"]:
                failures.append(f"Missing distributions: {', '.join(status['missing'])}")
    except DevError as error:
        failures.append(str(error))
    needs_sync = bool(failures)
    try:
        check_hooks()
    except DevError as error:
        failures.append(str(error))
    for failure in failures:
        print(f"dev: {failure}", file=sys.stderr)
    if failures:
        if needs_sync:
            print("dev: repair environment with: bash scripts/dev.sh sync", file=sys.stderr)
        return 1
    print(f"dev: development environment ready at {ROOT}")
    return 0


def main(arguments: list[str] | None = None) -> int:
    """Run the shared development CLI and preserve child failure statuses."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync", help="synchronize the locked development environment")
    sync.add_argument("--profile", choices=("dev", "lint", "postgres"), default="dev")
    sync.add_argument("--check", action="store_true", help="check synchronization without installing")
    commands.add_parser("doctor", help="check development readiness without changing it")
    check = commands.add_parser("check", help="run a shared static check")
    check.add_argument("name", choices=(*CHECKS, "all"))
    test = commands.add_parser("test", help="run explicit pytest targets/options")
    test.add_argument("arguments", nargs=argparse.REMAINDER)
    hooks = commands.add_parser("hooks", help="install or check worktree-aware hook dispatchers")
    hooks.add_argument("action", choices=("install", "check"))
    hooks.add_argument("--replace", action="store_true", help="back up existing hooks before replacing them")
    message = commands.add_parser("commit-msg", help="check a commit message with commitizen")
    message.add_argument("path", type=Path)
    args = parser.parse_args(arguments)
    try:
        if args.command == "sync":
            return sync_environment(check=args.check, profile=args.profile)
        if args.command == "doctor":
            return doctor()
        if args.command == "check":
            return run_check(args.name)
        if args.command == "test":
            return run_tests(args.arguments)
        if args.command == "hooks":
            return install_hooks(replace=args.replace) if args.action == "install" else check_hooks()
        if args.command == "commit-msg":
            message_path = args.path.resolve()
            if not message_path.is_file():
                raise DevError(f"Commit message file is missing: {message_path}")
            return _run(
                [str(_python()), "-m", "commitizen", "check", "--commit-msg-file", str(message_path)]
            ).returncode
    except (DevError, OSError, ValueError) as error:
        print(f"dev: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
