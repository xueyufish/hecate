"""Test-side expansion helper for the HTTP binding file.

``openapi_spec_validator`` resolves ``$ref`` targets itself and cannot fetch
the contract ``$id`` URIs, so the binding tests validate an EXPANDED copy:
every external contract reference is replaced by the referenced schema's
contents, bundled under ``components/schemas/<stem>``. The repository file
itself keeps only ``$id`` references - a single source of truth, no copied
field definitions. Expansion happens in test memory only and is mechanical:
- ``https://.../X.schema.json``            -> ``#/components/schemas/X``
- ``https://.../X.schema.json#/$defs/frag`` -> ``#/components/schemas/X/$defs/frag``
- internal ``#/$defs/frag`` inside an embedded schema -> ``#/components/schemas/X/$defs/frag``
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "src" / "hecate" / "contracts"
BINDING_PATH = CONTRACTS_DIR / "openapi" / "execution-backend.http.v0_1.yaml"
SCHEMA_DIR = CONTRACTS_DIR / "schemas"

_CONTRACT_URI_PREFIX = "https://hecate.dev/contracts/"


def load_binding() -> dict[str, Any]:
    return yaml.safe_load(BINDING_PATH.read_text(encoding="utf-8"))


def _schema_by_uri() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        out[contents["$id"]] = contents
    return out


def _stem_of(uri: str) -> str:
    return uri.rsplit("/", 1)[-1].removesuffix(".schema.json")


def expand_for_validation(doc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``doc`` with external contract refs expanded locally."""

    schemas_by_uri = _schema_by_uri()
    expanded = copy.deepcopy(doc)
    bundled: dict[str, dict[str, Any]] = {}

    def rewrite_local_pointer(value: str, owner_stem: str) -> str:
        """Point an embedded schema's internal ``#/$defs/...`` at its new home."""

        if value.startswith("#/$defs/"):
            return f"#/components/schemas/{owner_stem}/{value[2:]}"
        return value

    def embed(uri: str) -> str:
        stem = _stem_of(uri)
        if stem in bundled:
            return stem
        contents = copy.deepcopy(schemas_by_uri[uri])
        bundled[stem] = contents  # reserve before recursion (schemas are acyclic)
        stack = [(contents, stem)]
        while stack:
            node, owner = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "$ref" and isinstance(value, str):
                        if value.startswith("#/$defs/"):
                            node[key] = rewrite_local_pointer(value, owner)
                        elif value.startswith(_CONTRACT_URI_PREFIX):
                            base = value.split("#", 1)[0]
                            node[key] = f"#/components/schemas/{embed(base)}" + _local_pointer(value)
                        continue
                    if isinstance(value, (dict, list)):
                        stack.append((value, owner))
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, (dict, list)):
                        stack.append((item, owner))
        return stem

    def _local_pointer(value: str) -> str:
        _, _, fragment = value.partition("#")
        return fragment  # already carries its leading "/" (or is empty)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str) and value.startswith(_CONTRACT_URI_PREFIX):
                    base = value.split("#", 1)[0]
                    stem = embed(base)
                    node[key] = f"#/components/schemas/{stem}" + _local_pointer(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(expanded)
    components = expanded.setdefault("components", {}).setdefault("schemas", {})
    components.update(bundled)
    return expanded
