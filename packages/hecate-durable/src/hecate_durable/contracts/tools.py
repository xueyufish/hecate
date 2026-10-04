"""ToolDeclaration - cross-backend tool declaration contract.

The side-effect vocabulary is adopted verbatim from the internal
``SideEffectClass`` enum (``runtime/tool_side_effects.py``); a test pins the
two enums to stay identical. Failure layering: parameter validation fails
before dispatch; business rejection is a tool RESULT (run continues); system
failure is a tool-level error event; remote outcome unknown reuses
``outcome_unknown`` and reconciles by idempotent id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolSideEffectClass(StrEnum):
    """Retry semantics, mirroring ``runtime.tool_side_effects.SideEffectClass``."""

    READONLY = "readonly"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ToolDeclaration:
    """One tool's cross-backend declaration (name, version, schemas, class)."""

    tool_name: str
    version: str
    input_schema_ref: str
    output_schema_ref: str
    side_effect_class: ToolSideEffectClass
    description: str | None = None
    deprecation: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.side_effect_class, ToolSideEffectClass):
            raise ValueError(f"side_effect_class must be a ToolSideEffectClass, got {self.side_effect_class!r}")
        for name in ("tool_name", "version", "input_schema_ref", "output_schema_ref"):
            if not getattr(self, name):
                raise ValueError(f"tool declaration {name!r} must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "tool_name": self.tool_name,
            "version": self.version,
            "input_schema_ref": self.input_schema_ref,
            "output_schema_ref": self.output_schema_ref,
            "side_effect_class": self.side_effect_class.value,
        }
        if self.description is not None:
            out["description"] = self.description
        if self.deprecation is not None:
            out["deprecation"] = self.deprecation
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDeclaration:
        known = {
            "tool_name",
            "version",
            "input_schema_ref",
            "output_schema_ref",
            "side_effect_class",
            "description",
            "deprecation",
        }
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            tool_name=data["tool_name"],
            version=data["version"],
            input_schema_ref=data["input_schema_ref"],
            output_schema_ref=data["output_schema_ref"],
            side_effect_class=ToolSideEffectClass(data["side_effect_class"]),
            description=data.get("description"),
            deprecation=data.get("deprecation"),
            extra=extra,
        )
