"""Shared helpers for execution-contract tests.

Loads the authoritative schema files into a ``referencing`` Registry so
cross-file ``$ref`` (references.schema.json#/$defs/...) resolve by ``$id``,
and maps each standard sample to the schema (and fragment) that governs it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "src" / "hecate" / "contracts" / "schemas"
SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
EXECUTION_BASE = "https://hecate.dev/contracts/execution/0.1"
SANDBOX_BASE = "https://hecate.dev/contracts/sandbox/0.1"


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    """Registry holding every authoritative schema file under its ``$id``."""

    resources: list[tuple[str, Resource]] = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=1)
def schema_ids() -> set[str]:
    return {path.stem for path in SCHEMA_DIR.glob("*.schema.json")}


def schema_uri(name: str) -> str:
    """Full ``$id`` for one schema file stem (e.g. ``execution-request``).

    v0.2 files (errors, capabilities) carry their own version after the
    execution-backend-bindings change bumped them independently.
    """

    theme = "sandbox" if name == "sandbox" else "execution"
    version = "0.2" if name in {"errors", "capabilities"} else "0.1"
    return f"https://hecate.dev/contracts/{theme}/{version}/{name}.schema.json"


def validate_against_schema(instance: Any, schema_name: str, fragment: str = "") -> None:
    """Validate ``instance`` against an authoritative schema (or one of its defs)."""

    uri = schema_uri(schema_name)
    target: dict[str, Any] = (
        {"$ref": uri + fragment}
        if fragment
        else json.loads((SCHEMA_DIR / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    )
    validator = jsonschema.Draft202012Validator(target, registry=load_registry())
    validator.validate(instance)


def sample_schema_ref(path: Path) -> tuple[str, str] | None:
    """Map one standard sample to its governing schema name and fragment.

    ``None`` means the sample is not governed by a single schema file
    (currently: HTTP request/response pairs, validated by the binding tests).
    """

    theme = path.parent.name
    name = path.name
    if theme == "http":
        return None
    if theme == "references":
        return "references", "#/$defs/ref"
    if theme == "requests":
        return "execution-request", ""
    if theme == "events":
        return "event-envelope", ""
    if theme == "errors":
        return "errors", ""
    if theme == "capabilities":
        return "capabilities", ""
    if theme == "manifest":
        return "artifact-manifest", ""
    if theme == "security-claims":
        return "security-claims", ""
    if theme == "tools":
        return "tool", ""
    if theme == "sandbox":
        if "create-environment" in name:
            return "sandbox", "#/$defs/createEnvironmentRequest"
        if "sandbox-info" in name:
            return "sandbox", "#/$defs/sandboxInfo"
        return "sandbox", "#/$defs/commandRecord"
    raise AssertionError(f"unknown sample theme directory: {theme}")


def all_samples() -> list[Path]:
    return sorted(p for p in SAMPLES_DIR.rglob("*.json"))


def load_sample(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# --- live pilot interop (change execution-backend-nonpython-pilot) -----------

import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

from tests.test_execution.live_http import LiveHttpBackend  # noqa: E402

_PILOT_DIR = SCHEMA_DIR.parents[3] / "pilots" / "execution-backend-ts"


@pytest.fixture(scope="session")
def live_backend() -> LiveHttpBackend:
    """Run the TypeScript pilot on an ephemeral loopback port.

    Skips with a stated reason when node is unavailable - the A-side vitest
    suite is the standing proof; this fixture adds cross-language interop
    whenever a node runtime exists. It is a conditional skip, not a
    permanent one.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node runtime not available; live pilot interop requires node")
    dist_server = _PILOT_DIR / "dist" / "server.js"
    if not dist_server.exists():
        subprocess.run(  # noqa: S603 - node and args resolved from repo layout
            [
                node,
                str(_PILOT_DIR / "node_modules" / "typescript" / "bin" / "tsc"),
                "-p",
                str(_PILOT_DIR / "tsconfig.json"),
            ],
            check=True,
            cwd=_PILOT_DIR,
        )
    proc = subprocess.Popen(  # noqa: S603 - node and args resolved from repo layout
        [node, str(dist_server), "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        port = None
        deadline = time.monotonic() + 20
        assert proc.stdout is not None
        while port is None:
            if time.monotonic() > deadline:
                proc.kill()
                pytest.fail("pilot server did not report readiness in time")
            line = proc.stdout.readline()
            if not line:
                if port is None:
                    pytest.fail("pilot server exited before reporting readiness")
                break
            if "PILOT_LISTENING" in line:
                port = int(line.strip().rsplit("=", 1)[1])
        yield LiveHttpBackend(f"http://127.0.0.1:{port}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
