"""JSON-to-GraphConfig parsing pipeline with schema validation.

This module implements the first stage of the graph lifecycle: converting a raw
JSON definition (string or dict) into a typed GraphConfig dataclass. The pipeline
has two stages:

1. **JSON Schema validation** -- the raw data is validated against the Graph DSL
   JSON Schema (``schemas/graph-dsl.schema.json``) using jsonschema. Validation
   errors are wrapped in GraphValidationError with a ``field`` attribute that
   carries the dotted JSON path for error localization in user-facing messages.
2. **Typed construction** -- the validated dict is converted into ChannelDef,
   NodeConfig, Edge, and GraphConfig dataclasses.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import jsonschema

from hecate.engine.types import (
    ChannelDef,
    ChannelType,
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
    RoutingMode,
)

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "schemas" / "graph-dsl.schema.json"


class GraphValidationError(Exception):
    """Raised when a graph definition fails schema or structural validation.

    Attributes:
        field: Dotted JSON path pointing to the invalid element (e.g.
            ``"nodes.guard.config.model"``). Carried from jsonschema's
            ``absolute_path`` for user-facing error localization. None when
            the error applies to the entire document.
    """

    def __init__(self, message: str, field: str | None = None):
        self.field = field
        super().__init__(message)


def _load_schema() -> dict:
    """Load the Graph DSL JSON Schema from disk.

    The schema file is located at ``schemas/graph-dsl.schema.json`` relative
    to the project root (computed from this module's file path).
    """
    with open(SCHEMA_PATH) as f:
        return json.load(f)


def parse_graph(raw: str | dict) -> GraphConfig:
    """Parse and validate a JSON graph definition into a GraphConfig.

    The pipeline proceeds through two stages:

    1. **Deserialization** -- if ``raw`` is a string, it is parsed as JSON.
       Pre-parsed dicts are accepted directly.
    2. **Schema validation** -- the data is validated against the Graph DSL
       JSON Schema. On failure, a GraphValidationError is raised with the
       ``field`` attribute set to the dotted path of the offending element.

    Args:
        raw: A JSON string or a pre-parsed dict representing a graph definition.

    Returns:
        A fully typed GraphConfig.

    Raises:
        GraphValidationError: if the input is malformed or fails schema validation.
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
        raw_type = defn["type"]
        persistent = defn.get("persistent", False)

        # Auto-migrate deprecated "persistent_topic"
        if raw_type == "persistent_topic":
            logger.warning(
                "Channel type 'persistent_topic' is deprecated. "
                "Use 'topic' with 'persistent: true' instead. "
                f"Migrating channel '{name}' automatically."
            )
            raw_type = "topic"
            persistent = True

        state[name] = ChannelDef(
            type=ChannelType(raw_type),
            default=defn.get("default"),
            initial=defn.get("initial"),
            reduce_fn=defn.get("reduce"),
            persistent=persistent,
        )

    nodes = {}
    for node_id, node_data in data.get("nodes", {}).items():
        nodes[node_id] = NodeConfig(
            id=node_id,
            type=NodeType(node_data["type"]),
            config=node_data.get("config", {}),
        )

    for node_id, node_data in data.get("nodes", {}).items():
        if node_data["type"] == "condition":
            config = node_data.get("config", {})
            rm = config.get("routing_mode")
            if rm is not None:
                try:
                    RoutingMode(rm)
                except ValueError:
                    raise GraphValidationError(
                        f"Invalid routing_mode '{rm}' for node '{node_id}'. Must be one of: condition, intent, dynamic",
                        field=f"nodes[{node_id}].config.routing_mode",
                    ) from None

    edges = []
    for edge_data in data.get("edges", []):
        edges.append(
            Edge(
                source=edge_data["source"],
                target=edge_data.get("target"),
                trigger=edge_data.get("trigger") or edge_data.get("type"),
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
