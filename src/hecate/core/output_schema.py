"""Offline JSON Schema checks for user-declared execution output contracts."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator, SchemaError


def check_output_schema(schema: dict[str, Any]) -> None:
    """Check schema syntax and prohibit network retrieval during validation."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"invalid output_schema: {exc.message}") from exc
    pending: list[Any] = [schema]
    single_schema = (
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "items",
        "additionalItems",
        "unevaluatedItems",
        "contentSchema",
    )
    schema_maps = ("$defs", "definitions", "properties", "patternProperties", "dependentSchemas", "dependencies")
    schema_arrays = ("allOf", "anyOf", "oneOf", "prefixItems")
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for keyword in ("$ref", "$dynamicRef"):
                reference = item.get(keyword)
                if reference is not None and (not isinstance(reference, str) or not reference.startswith("#")):
                    raise ValueError("output_schema references must be local fragments")
            # Data in const/examples and property names are not schema keywords.
            pending.extend(item[key] for key in single_schema if key in item)
            for key in schema_maps:
                children = item.get(key)
                if isinstance(children, dict):
                    pending.extend(children.values())
            for key in schema_arrays:
                children = item.get(key)
                if isinstance(children, list):
                    pending.extend(children)
        elif isinstance(item, list):
            pending.extend(item)


def output_schema_error(document: Any, schema: dict[str, Any]) -> str | None:
    """Return a validation or reference-resolution violation without fetching URLs."""
    try:
        check_output_schema(schema)
        error = next(Draft202012Validator(schema).iter_errors(document), None)
    except Exception as exc:
        # Unresolvable local references are invalid contracts, not server errors.
        return f"output schema cannot be evaluated: {exc}"
    return error.message if error is not None else None
