"""Harness context composition: declarative sections, digest, determinism.

Covers the composition half of the harness layer: fail-fast validation of
caller declarations and final specs, digest stability (identical
compositions share a digest, any edit does not), deterministic rendering
that keeps the platform's historical base-input shape byte-compatible,
and strict round-trips that fail closed on tampering. The binding behavior
(decisions pinning ``harness_version``/``harness_digest``) lives in the
controller suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hecate_runtime.agent_harness import (
    HarnessDeclaration,
    HarnessSection,
    HarnessSpec,
    HarnessSpecError,
    declared_harness,
    validate_harness_declaration,
)


def _spec(*sections: HarnessSection, version: int = 1) -> HarnessSpec:
    return HarnessSpec(version=version, sections=sections)


def _full(persona: str = "You are a helper.", task_text: str = "do it") -> HarnessSpec:
    return _spec(
        HarnessSection("persona", persona),
        HarnessSection("task", [{"role": "user", "content": task_text}]),
    )


def test_unknown_section_type_is_an_authoring_error() -> None:
    with pytest.raises(HarnessSpecError, match="unknown harness section type"):
        HarnessSection("planner", "be smart")


def test_section_content_is_validated_per_type() -> None:
    with pytest.raises(HarnessSpecError, match="nonblank string"):
        HarnessSection("persona", "  ")
    with pytest.raises(HarnessSpecError, match="nonblank string"):
        HarnessSection("instructions", None)
    with pytest.raises(HarnessSpecError, match="JSON object"):
        HarnessSection("output_schema", ["not-an-object"])
    with pytest.raises(HarnessSpecError, match="nonempty list"):
        HarnessSection("task", [])
    with pytest.raises(HarnessSpecError, match="JSON objects"):
        HarnessSection("task", ["not-an-object"])
    with pytest.raises(HarnessSpecError, match="JSON value"):
        HarnessSection("memory", {"facts": {1, "not-json"}})


def test_spec_requires_exactly_one_task_section_last() -> None:
    task = HarnessSection("task", [{"role": "user", "content": "x"}])
    with pytest.raises(HarnessSpecError, match="exactly one task section"):
        _spec(HarnessSection("persona", "p"))
    with pytest.raises(HarnessSpecError, match="exactly one task section"):
        _spec(task, task)
    with pytest.raises(HarnessSpecError, match="exactly one task section"):
        _spec(task, HarnessSection("persona", "p"))


def test_declaration_cannot_author_host_owned_sections() -> None:
    with pytest.raises(HarnessSpecError, match="cannot author"):
        HarnessDeclaration(1, (HarnessSection("task", [{"role": "user", "content": "x"}]),))
    with pytest.raises(HarnessSpecError, match="cannot author"):
        HarnessDeclaration(1, (HarnessSection("output_schema", {"type": "object"}),))


def test_digest_is_stable_and_content_sensitive() -> None:
    base = _full()
    assert _full().sha256 == base.sha256
    assert _full(persona="different").sha256 != base.sha256
    assert _full(task_text="other").sha256 != base.sha256
    assert (
        _spec(
            HarnessSection("persona", "You are a helper."),
            HarnessSection("task", [{"role": "user", "content": "do it"}]),
            version=2,
        ).sha256
        != base.sha256
    )
    # Section order is part of the composition identity.
    reordered = _spec(
        HarnessSection("task", [{"role": "user", "content": "do it"}]),
    )
    assert reordered.sha256 != base.sha256


def test_compose_renders_platform_legacy_shape_byte_for_byte() -> None:
    """A default declaration composes exactly the platform's manual build."""
    spec = declared_harness({"version": 1}).compose_spec(
        HarnessSection("persona", "You are a helpful assistant."),
        HarnessSection("task", [{"role": "user", "content": "check it"}]),
    )
    assert spec.compose_messages() == [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "check it"},
    ]
    schema_spec = declared_harness({}).compose_spec(
        HarnessSection("persona", "You are a helpful assistant."),
        HarnessSection("output_schema", {"type": "object"}),
        HarnessSection("task", [{"role": "user", "content": "check it"}]),
    )
    assert schema_spec.compose_messages()[1] == {
        "role": "system",
        "content": "Return final output as JSON satisfying this schema: " + json.dumps({"type": "object"}),
    }


def test_compose_keeps_declared_section_order_and_envelopes() -> None:
    spec = _spec(
        HarnessSection("instructions", "Plan before acting."),
        HarnessSection("skills", [{"name": "search"}]),
        HarnessSection("memory", {"facts": ["f1"]}),
        HarnessSection("persona", "You are a helper."),
        HarnessSection("task", [{"role": "user", "content": "x"}]),
    )
    messages = spec.compose_messages()
    assert [message["role"] for message in messages] == ["system"] * 4 + ["user"]
    assert messages[0]["content"] == "Plan before acting."
    assert messages[1]["content"] == '{"skills":[{"name":"search"}]}'
    assert messages[2]["content"] == '{"memory":{"facts":["f1"]}}'
    assert messages[3]["content"] == "You are a helper."
    # Composition never mutates the frozen sections.
    assert spec.sections[1].content == [{"name": "search"}]


def test_task_messages_append_verbatim_including_multiple_entries() -> None:
    task_messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "earlier"},
        {"role": "user", "content": "second"},
    ]
    spec = _spec(HarnessSection("task", task_messages))
    rendered = spec.compose_messages()
    assert rendered == json.loads(json.dumps(task_messages))
    rendered[0]["content"] = "mutated"
    assert spec.compose_messages()[0]["content"] == "first"


def test_round_trip_preserves_composition_and_detects_tampering() -> None:
    spec = _full()
    restored = HarnessSpec.from_dict(spec.to_dict())
    assert restored.sha256 == spec.sha256
    assert restored.compose_messages() == spec.compose_messages()

    tampered = spec.to_dict()
    tampered["sha256"] = "0" * 64
    with pytest.raises(HarnessSpecError, match="digest mismatch"):
        HarnessSpec.from_dict(tampered)

    with pytest.raises(HarnessSpecError, match="unknown field"):
        HarnessSpec.from_dict({"version": 1, "sections": [], "planner": {}})


def test_declaration_round_trip_and_strict_fields() -> None:
    declaration = declared_harness({"version": 3, "sections": [{"type": "instructions", "content": "Be terse."}]})
    assert declaration is not None
    assert declared_harness(declaration.to_dict()) == declaration
    assert declared_harness(None) is None
    assert declared_harness({}).sections == ()

    with pytest.raises(HarnessSpecError, match="unknown field"):
        declared_harness({"version": 1, "sections": [], "persona": "x"})
    with pytest.raises(HarnessSpecError, match="positive integer"):
        declared_harness({"version": 0})
    with pytest.raises(HarnessSpecError, match="must be an object"):
        declared_harness(["not-an-object"])


def test_validate_harness_declaration_restricts_authorable_types() -> None:
    block = {"sections": [{"type": "instructions", "content": "Be terse."}]}
    validate_harness_declaration(block)
    validate_harness_declaration(block, allowed_types=frozenset({"instructions"}))
    with pytest.raises(HarnessSpecError, match="not authorable"):
        validate_harness_declaration(
            {"sections": [{"type": "persona", "content": "p"}]},
            allowed_types=frozenset({"instructions", "skills", "memory"}),
        )
    validate_harness_declaration(None)


def test_composition_detaches_caller_owned_nested_content() -> None:
    messages = [{"role": "user", "content": "original"}]
    task = HarnessSection("task", messages)
    spec = _spec(task)
    digest = spec.sha256
    messages[0]["content"] = "changed source"
    task.content[0]["content"] = "changed section"
    assert spec.compose_messages() == [{"role": "user", "content": "original"}]
    assert spec.to_dict()["sha256"] == digest


def test_declaration_detaches_content_and_each_composed_spec() -> None:
    block: dict[str, Any] = {"sections": [{"type": "memory", "content": {"facts": ["original"]}}]}
    declaration = declared_harness(block)
    assert declaration is not None
    task = HarnessSection("task", [{"role": "user", "content": "x"}])
    spec = declaration.compose_spec(task)
    block["sections"][0]["content"]["facts"].append("changed source")
    assert declaration.to_dict()["sections"][0]["content"] == {"facts": ["original"]}
    declaration.sections[0].content["facts"].append("changed declaration")
    assert spec.compose_messages()[0]["content"] == '{"memory":{"facts":["original"]}}'
    spec.to_dict()


def test_tampered_spec_cannot_render_messages() -> None:
    spec = _full()
    spec.sections[-1].content[0]["content"] = "tampered"
    with pytest.raises(HarnessSpecError, match="digest mismatch"):
        spec.compose_messages()


def test_equivalent_schema_key_orders_render_identical_messages() -> None:
    first = {"type": "object", "properties": {"x": {"type": "string", "description": "field"}}}
    reordered = {"properties": {"x": {"description": "field", "type": "string"}}, "type": "object"}
    task = HarnessSection("task", [{"role": "user", "content": "x"}])
    a = _spec(HarnessSection("output_schema", first), task)
    b = _spec(HarnessSection("output_schema", reordered), task)
    assert a.sha256 == b.sha256
    assert a.compose_messages() == b.compose_messages()
