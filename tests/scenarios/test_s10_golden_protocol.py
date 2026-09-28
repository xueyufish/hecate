"""S10 — old-path protocol golden samples assert structure, never text.

Manifest: S10 (P08). Golden files pin the raw chat path's response protocol
structure (non-stream body skeleton + stream chunk skeleton + terminator) so
later entry convergence (step5) has an explicit migration baseline.

Recording mode: run pytest with ``HECATE_SCENARIO_RECORD_GOLDENS=1`` to
regenerate ``goldens/chat_protocol.json`` from a live stub-model run. CI
always compares. Assertions are structural by construction (skeleton
comparison); model text is never compared.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tests.scenarios.conftest import chat_body
from tests.scenarios.tools.protocol_skeleton import matches, skeleton

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "goldens", "chat_protocol.json")
RECORD_ENV = "HECATE_SCENARIO_RECORD_GOLDENS"


async def _capture_non_stream(scenario_client, scripted_llm) -> dict[str, Any]:
    async def responder(messages):
        from tests.scenarios.conftest import llm_response

        return llm_response(content="A deterministic summary of the material.")

    scripted_llm(responder)
    response = await scenario_client.post("/v1/chat/completions", json=chat_body("Summarize the material."))
    assert response.status_code == 200
    return response.json()


async def _capture_stream(scenario_client, scripted_llm) -> dict[str, Any]:
    async def stream_responder(messages):
        yield {"content": "A deterministic summary", "finish_reason": None}
        yield {"content": " of the material.", "finish_reason": None}

    async def responder(messages):
        raise RuntimeError("non-stream chat must not run in the stream capture")

    scripted_llm(responder, stream_responder)
    response = await scenario_client.post(
        "/v1/chat/completions",
        json={**chat_body("Summarize the material."), "stream": True},
    )
    assert response.status_code == 200

    chunks: list[dict[str, Any]] = []
    terminated_by_done = False
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ").strip()
        if payload == "[DONE]":
            terminated_by_done = True
            break
        chunks.append(json.loads(payload))

    assert chunks, "stream must yield at least one chunk"
    return {
        "stream_first_chunk": skeleton(chunks[0]),
        "stream_last_chunk_finish_reason": chunks[-1]["choices"][0]["finish_reason"],
        "stream_terminated_by_done": terminated_by_done,
    }


async def test_s10_golden_protocol_structure(scenario_client, scripted_llm) -> None:
    non_stream = await _capture_non_stream(scenario_client, scripted_llm)
    # Re-install: the second capture needs its own scripted service.
    stream = await _capture_stream(scenario_client, scripted_llm)

    if os.environ.get(RECORD_ENV):
        golden = {
            "_meta": {
                "purpose": "Old chat-path response protocol structure (step5 migration baseline).",
                "recorded_with": "scripted stub LLM, in-process ASGI transport",
                "code_paths": ["src/hecate/channel/api/v1/chat.py"],
                "text_policy": (
                    "Skeletons carry key sets and value types only; text-bearing values are "
                    "deliberately absent. Assertions compare structure, never model text."
                ),
            },
            "non_stream_response": skeleton(non_stream),
            **stream,
        }
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w", encoding="utf-8") as fh:
            json.dump(golden, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return

    with open(GOLDEN_PATH, encoding="utf-8") as fh:
        golden = json.load(fh)

    assert "text" not in json.dumps(skeleton(non_stream)), "skeletons must not embed text values"
    assert golden["non_stream_response"] == skeleton(non_stream), "non-stream protocol drifted from the golden baseline"
    assert golden["stream_first_chunk"] == stream["stream_first_chunk"], (
        "stream chunk protocol drifted from the golden baseline"
    )
    assert stream["stream_last_chunk_finish_reason"] == "stop"
    assert stream["stream_terminated_by_done"] is golden["stream_terminated_by_done"]


def test_s10_golden_boundary_checks() -> None:
    """The comparator's edges: text changes pass, structural drift fails."""
    recorded = {
        "choices": {"list_of": {"message": {"role": "str", "content": "str"}, "finish_reason": "str"}},
    }
    original = {"choices": [{"message": {"role": "assistant", "content": "Original text."}, "finish_reason": "stop"}]}
    changed_text = {
        "choices": [
            {"message": {"role": "assistant", "content": "Completely different text."}, "finish_reason": "stop"}
        ]
    }
    missing_field = {"choices": [{"message": {"role": "assistant"}, "finish_reason": "stop"}]}
    retyped_field = {"choices": [{"message": {"role": "assistant", "content": 42}, "finish_reason": "stop"}]}

    assert matches(recorded, original), "recorded structure must match its own source"
    assert matches(recorded, changed_text), "model text changes must NOT break the golden"
    assert not matches(recorded, missing_field), "a removed protocol field MUST break the golden"
    assert not matches(recorded, retyped_field), "a retyped protocol field MUST break the golden"
