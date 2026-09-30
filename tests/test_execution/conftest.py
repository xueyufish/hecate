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
    """Full ``$id`` for one schema file stem (e.g. ``execution-request``)."""

    theme = "sandbox" if name == "sandbox" else "execution"
    return f"https://hecate.dev/contracts/{theme}/0.1/{name}.schema.json"


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


def sample_schema_ref(path: Path) -> tuple[str, str]:
    """Map one standard sample to its governing schema name and fragment."""

    theme = path.parent.name
    name = path.name
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
