"""S04 — backend loss: model backend unreachable surfaces as explicit failure.

Manifest: S04 (P08). When the execution backend fails, the entry point must
return the platform error envelope — never a fabricated completion or a
partial result presented as success.
"""

from __future__ import annotations

from tests.scenarios.conftest import chat_body


async def test_s04_backend_loss_explicit_failure(scenario_error_client, scripted_llm) -> None:
    async def unreachable_backend(messages):
        raise ConnectionError("model backend unreachable")

    scripted_llm(unreachable_backend)
    response = await scenario_error_client.post(
        "/v1/chat/completions",
        json=chat_body("Say hello."),
    )

    assert response.status_code == 500, "backend loss must not masquerade as success"
    data = response.json()
    assert data["error"]["code"] == "INTERNAL_ERROR"
    assert data["error"]["details"] is None
    assert "choices" not in data, "no completion body may accompany a backend failure"
