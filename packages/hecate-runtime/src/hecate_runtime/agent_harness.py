"""Shared semantic context composition for the agent controller loop.

Both hosts (the platform dispatcher and the Runner engine) compose the
frozen base input of a controller run through this module. Composition is
declarative and content-complete: a caller-authored declaration carries
the semantic sections (persona, instructions, skills, memory) verbatim in
the durable task payload, and each host appends the sections it owns from
already-frozen facts (the platform: agent persona, output schema and task
messages; the Runner: the task input). The final spec is a pure function
of those inputs, so a re-dispatch after a restart composes byte-identical
base messages.

The binding mirrors the versioned acceptance gate: only an explicitly
declared composition pins ``harness_version`` and ``harness_digest`` on
every decision's ``input_refs``. A spec modified under the same version
number can therefore never silently reinterpret a committed chain — the
replay conflicts and stops for reconciliation — while chains committed
before any declaration (or by hosts that compose manually) keep replaying
byte-for-byte. The controller additionally requires the base messages it
is handed to equal the frozen composition, so the rendered input and the
pinned plan cannot drift apart.

Recovery facts never enter the composition: durable input revisions ride
``ControllerRunSpec.input_revisions`` at their causal turn boundaries and
the acceptance binding rides its own declaration. Projection here shapes
only the semantic base; it deletes no recovery state.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from hecate_runtime.accepted_decision import canonical_json

__all__ = [
    "HARNESS_SECTION_TYPES",
    "HarnessDeclaration",
    "HarnessSection",
    "HarnessSpec",
    "HarnessSpecError",
    "declared_harness",
    "validate_harness_declaration",
]

_OUTPUT_SCHEMA_MESSAGE = "Return final output as JSON satisfying this schema: "


class HarnessSpecError(ValueError):
    """Raised at declaration time for invalid harness composition blocks."""


def _require_json(value: Any, label: str) -> None:
    try:
        canonical_json(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise HarnessSpecError(f"{label} must be a JSON value") from exc


def _validate_persona(section: HarnessSection) -> None:
    if not isinstance(section.content, str) or not section.content.strip():
        raise HarnessSpecError("persona section content must be a nonblank string")


def _validate_instructions(section: HarnessSection) -> None:
    if not isinstance(section.content, str) or not section.content.strip():
        raise HarnessSpecError("instructions section content must be a nonblank string")


def _validate_skills(section: HarnessSection) -> None:
    _require_json(section.content, "skills section content")


def _validate_memory(section: HarnessSection) -> None:
    _require_json(section.content, "memory section content")


def _validate_output_schema(section: HarnessSection) -> None:
    if not isinstance(section.content, dict):
        raise HarnessSpecError("output_schema section content must be a JSON object")


def _validate_task(section: HarnessSection) -> None:
    messages = section.content
    if not isinstance(messages, list) or not messages:
        raise HarnessSpecError("task section content must be a nonempty list of messages")
    for message in messages:
        if not isinstance(message, dict):
            raise HarnessSpecError("task section messages must be JSON objects")
        _require_json(message, "task section message")


# Closed section registry — the composition trust boundary. A section type
# unknown here is an authoring error at declaration time, never a silent
# pass-through of caller-named semantics into the frozen base input.
HARNESS_SECTION_TYPES: dict[str, Any] = {
    "persona": _validate_persona,
    "instructions": _validate_instructions,
    "skills": _validate_skills,
    "memory": _validate_memory,
    "output_schema": _validate_output_schema,
    "task": _validate_task,
}

_SEMANTIC_SECTION_TYPES = frozenset({"persona", "instructions", "skills", "memory"})


@dataclass(frozen=True)
class HarnessSection:
    """One typed composition unit; content shape is validated per type."""

    type: str
    content: Any

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or self.type not in HARNESS_SECTION_TYPES:
            raise HarnessSpecError(
                f"unknown harness section type {self.type!r}; allowed: {sorted(HARNESS_SECTION_TYPES)}"
            )
        HARNESS_SECTION_TYPES[self.type](self)
        _require_json(self.content, f"{self.type} section content")
        object.__setattr__(self, "content", copy.deepcopy(self.content))

    def to_dict(self) -> dict[str, Any]:
        """Detached JSON form (``{"type", "content"}``)."""
        return {"type": self.type, "content": copy.deepcopy(self.content)}

    @classmethod
    def from_dict(cls, data: Any) -> HarnessSection:
        """Strict parse: unknown fields and unknown types are authoring errors."""
        if not isinstance(data, dict):
            raise HarnessSpecError("harness section entries must be objects")
        extras = set(data) - {"type", "content"}
        if extras:
            raise HarnessSpecError(f"harness section has unknown field(s) {sorted(extras)}")
        if "content" not in data:
            raise HarnessSpecError("harness section requires a content field")
        return cls(type=data.get("type"), content=data.get("content"))


@dataclass(frozen=True)
class HarnessSpec:
    """The final frozen composition: version plus fully ordered sections.

    Exactly one ``task`` section terminates the list — the model-visible
    task input is always last, so durable input revisions and later turn
    projections append strictly after the frozen base. ``sha256`` digests
    the canonical form of version and all section contents; two specs share
    a digest only when they are the same composition.
    """

    version: int
    sections: tuple[HarnessSection, ...]
    sha256: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise HarnessSpecError("harness.version must be a positive integer")
        if not isinstance(self.sections, tuple) or not self.sections:
            raise HarnessSpecError("harness sections must be a nonempty array")
        if any(not isinstance(section, HarnessSection) for section in self.sections):
            raise HarnessSpecError("harness sections must contain HarnessSection values")
        object.__setattr__(self, "sections", copy.deepcopy(self.sections))
        task_positions = [index for index, section in enumerate(self.sections) if section.type == "task"]
        if task_positions != [len(self.sections) - 1]:
            raise HarnessSpecError("harness composition requires exactly one task section, ordered last")
        object.__setattr__(self, "sha256", _spec_digest(self.version, self.sections))

    def compose_messages(self) -> list[dict[str, Any]]:
        """Render the deterministic base messages for the controller loop.

        A pure projection of the frozen sections: semantic sections become
        system messages in order (skills and memory ride identifiable JSON
        envelopes), the output-schema section keeps the platform's
        historical instruction wording, and the task section's messages
        append verbatim. The result never embeds recovery facts — those
        ride their own bindings.
        """
        self.to_dict()
        messages: list[dict[str, Any]] = []
        for section in self.sections:
            if section.type in ("persona", "instructions"):
                messages.append({"role": "system", "content": section.content})
            elif section.type in ("skills", "memory"):
                messages.append(
                    {
                        "role": "system",
                        "content": canonical_json({section.type: section.content}),
                    }
                )
            elif section.type == "output_schema":
                messages.append(
                    {"role": "system", "content": _OUTPUT_SCHEMA_MESSAGE + json.dumps(section.content, sort_keys=True)}
                )
            else:
                messages.extend(copy.deepcopy(section.content))
        return messages

    def to_dict(self) -> dict[str, Any]:
        """Return the detached declaration after checking its frozen digest."""
        if self.sha256 != _spec_digest(self.version, self.sections):
            raise HarnessSpecError("harness spec digest mismatch")
        return {
            "version": self.version,
            "sections": [section.to_dict() for section in self.sections],
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: Any) -> HarnessSpec:
        """Strict parse of a persisted full spec (admission validated earlier).

        A persisted ``sha256`` is verified against the recomputed digest, so
        a spec tampered with in storage fails closed instead of rebinding.
        """
        if not isinstance(data, dict):
            raise HarnessSpecError("harness spec must be an object")
        extras = set(data) - {"version", "sections", "sha256"}
        if extras:
            raise HarnessSpecError(f"harness spec has unknown field(s) {sorted(extras)}")
        sections = data.get("sections")
        if not isinstance(sections, list):
            raise HarnessSpecError("harness.sections must be an array")
        spec = cls(version=data.get("version", 1), sections=tuple(HarnessSection.from_dict(item) for item in sections))
        persisted_digest = data.get("sha256")
        if persisted_digest is not None and persisted_digest != spec.sha256:
            raise HarnessSpecError("harness spec digest mismatch")
        return spec


def _spec_digest(version: int, sections: tuple[HarnessSection, ...]) -> str:
    payload = canonical_json({"version": version, "sections": [section.to_dict() for section in sections]})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HarnessDeclaration:
    """The caller-authored half of the composition, frozen in the task payload.

    Only semantic sections may be declared here; host-owned sections
    (output schema, task input, and on the platform the agent persona)
    are derived at dispatch from already-frozen facts, so a restart
    rebuilds the same final spec without re-reading live configuration.
    """

    version: int
    sections: tuple[HarnessSection, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version < 1:
            raise HarnessSpecError("harness.version must be a positive integer")
        if not isinstance(self.sections, tuple):
            raise HarnessSpecError("harness.sections must be an array")
        if any(not isinstance(section, HarnessSection) for section in self.sections):
            raise HarnessSpecError("harness sections must contain HarnessSection values")
        object.__setattr__(self, "sections", copy.deepcopy(self.sections))
        for section in self.sections:
            if section.type not in _SEMANTIC_SECTION_TYPES:
                raise HarnessSpecError(
                    f"harness declaration cannot author a {section.type!r} section; "
                    f"allowed: {sorted(_SEMANTIC_SECTION_TYPES)}"
                )

    def to_dict(self) -> dict[str, Any]:
        """Detached JSON form of the declaration half."""
        return {"version": self.version, "sections": [section.to_dict() for section in self.sections]}

    def compose_spec(self, *host_sections: HarnessSection) -> HarnessSpec:
        """Freeze the final spec by appending the host-derived sections.

        Host sections must not include a ``task`` section other than as the
        final argument — the returned spec's own validation enforces the
        exactly-one-task-last rule, so a malformed derivation is an error,
        never a silently reordered composition.
        """
        return HarnessSpec(version=self.version, sections=self.sections + tuple(host_sections))


def _parse_declaration(data: Any) -> HarnessDeclaration:
    if not isinstance(data, dict):
        raise HarnessSpecError("harness must be an object")
    extras = set(data) - {"version", "sections"}
    if extras:
        raise HarnessSpecError(f"harness declaration has unknown field(s) {sorted(extras)}")
    sections = data.get("sections", [])
    if not isinstance(sections, list):
        raise HarnessSpecError("harness.sections must be an array")
    return HarnessDeclaration(
        version=data.get("version", 1),
        sections=tuple(HarnessSection.from_dict(item) for item in sections),
    )


def validate_harness_declaration(data: Any, *, allowed_types: frozenset[str] | None = None) -> None:
    """Fail fast on a malformed caller declaration at submission time.

    Raises :class:`HarnessSpecError` before any model usage; ``None``
    (no declaration) is always valid. Hosts narrow ``allowed_types`` to
    the section types their callers may author — a host that derives a
    section from its own frozen facts (e.g. the platform's agent persona)
    rejects caller-authored sections of that type here.
    """
    if data is None:
        return
    declaration = _parse_declaration(data)
    allowed = _SEMANTIC_SECTION_TYPES if allowed_types is None else frozenset(allowed_types)
    for section in declaration.sections:
        if section.type not in allowed:
            raise HarnessSpecError(
                f"harness section type {section.type!r} is not authorable here; allowed: {sorted(allowed)}"
            )


def declared_harness(data: Any) -> HarnessDeclaration | None:
    """The frozen declaration half, or ``None`` when none was declared.

    This is the binding source: only an explicitly declared block composes
    through the harness and pins ``harness_version``/``harness_digest`` on
    decisions. Chains committed without a declaration keep their host's
    manual composition and replay byte-for-byte. The stored block was
    validated at admission; a block that no longer parses is storage
    corruption and fails closed.
    """
    if data is None:
        return None
    return _parse_declaration(data)
