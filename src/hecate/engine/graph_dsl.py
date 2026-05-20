from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from hecate.engine.types import (
    ChannelDef,
    ChannelType,
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
)

SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "schemas" / "graph-dsl.schema.json"


class GraphValidationError(Exception):
    """Raised when a graph definition fails schema or structural validation."""

    def __init__(self, message: str, field: str | None = None):
        self.field = field
        super().__init__(message)


def _load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def parse_graph(raw: str | dict) -> GraphConfig:
    """Parse and validate a JSON graph definition into a GraphConfig.

    Accepts a JSON string or a pre-parsed dict. Validates against the
    Graph DSL JSON Schema and converts the result into typed dataclasses.
    """
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise GraphValidationError(f"Invalid JSON: {e}") from e
    else:
        data = raw

    schema = _load_schema()
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as e:
        field = ".".join(str(p) for p in e.absolute_path) if e.absolute_path else None
        raise GraphValidationError(e.message, field=field) from e

    state = {}
    for name, defn in data.get("state", {}).items():
        state[name] = ChannelDef(
            type=ChannelType(defn["type"]),
            default=defn.get("default"),
            initial=defn.get("initial"),
            reduce_fn=defn.get("reduce"),
        )

    nodes = {}
    for node_id, node_data in data.get("nodes", {}).items():
        nodes[node_id] = NodeConfig(
            id=node_id,
            type=NodeType(node_data["type"]),
            config=node_data.get("config", {}),
        )

    edges = []
    for edge_data in data.get("edges", []):
        edges.append(
            Edge(
                source=edge_data["source"],
                target=edge_data["target"],
                trigger=edge_data.get("trigger"),
            )
        )

    return GraphConfig(
        version=data.get("version", "1.0"),
        name=data.get("name", ""),
        state=state,
        nodes=nodes,
        edges=edges,
        entry=data.get("entry", ""),
    )
