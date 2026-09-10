"""Tests for AI dataset synthesis REST API endpoints."""

from __future__ import annotations

import json
import uuid

import pytest


def _stub_dlp():
    class _StubDLP:
        def __init__(self, *_a: object, **_kw: object) -> None:
            pass

        async def dry_run_scan(self, *_a: object, **_kw: object) -> object:
            class _R:
                findings: list = []

            return _R()

    return _StubDLP


class TestSynthesisEndpoint:
    async def test_sync_path_returns_200_with_summary(self, client: object, monkeypatch: pytest.MonkeyPatch) -> None:
        """count=10 (≤20) → synchronous response with items_generated."""
        import hecate_llm.service

        from hecate.ops.dlp import service as dlp_mod

        # Both strategy and quality filter share one chat stub. The
        # strategy runs first (10 calls for count=10); the resulting
        # ``items`` lack "query"/"expected_answer" because the quality
        # payload is returned. To make all 10 items survive we cycle:
        # first 10 calls return strategy, the rest return quality.
        counter = {"n": 0}
        strategy = json.dumps({"query": "What is X?", "expected_answer": "X is a thing"})
        quality = json.dumps({"self_containment": 1.0, "clarity": 1.0})

        async def _chat(*_a: object, **_kw: object) -> object:
            counter["n"] += 1
            payload = strategy if counter["n"] <= 10 else quality

            class _R:
                content = payload

            return _R

        monkeypatch.setattr(hecate_llm.service.llm_service, "chat", _chat)
        monkeypatch.setattr(dlp_mod, "DLPService", _stub_dlp())

        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets/synthesize",
            json={
                "strategy": "generation",
                "topic": "SSO",
                "count": 10,
                "target_dataset_name": "api-test-sync",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "target_dataset_id" in data
        assert data["items_generated"] == 10
        assert data["items_filtered"] == 0

    async def test_async_path_returns_202_with_job_id(self, client: object, monkeypatch: pytest.MonkeyPatch) -> None:
        """count=50 (>20) → 202 + job_id."""
        # Stub out run_job_in_background so we don't fire an actual
        # background task (test env has no Postgres for async_session_factory).
        from hecate.ops.evaluation.synthesis import job as job_mod

        async def _noop(self: object, job_id: object) -> None:  # noqa: ARG001
            return None

        monkeypatch.setattr(job_mod.DatasetSynthesisJobService, "run_job_in_background", _noop)

        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets/synthesize",
            json={
                "strategy": "generation",
                "topic": "SSO",
                "count": 50,
                "target_dataset_name": "api-test-async",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "queued"

    async def test_count_zero_returns_422(self, client: object) -> None:
        """count must be 1-100 per Pydantic schema — 0 is rejected by
        validation BEFORE the request reaches the handler."""
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets/synthesize",
            json={
                "strategy": "generation",
                "topic": "SSO",
                "count": 0,
                "target_dataset_name": "bad",
            },
        )
        assert response.status_code == 422

    async def test_adversarial_missing_intent_returns_400(self, client: object) -> None:
        """The handler validates adversarial_intent for adversarial
        strategy and returns 400 Bad Request (service-layer ValueError
        — semantic rejection, not schema-level validation)."""
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets/synthesize",
            json={
                "strategy": "adversarial",
                "count": 5,
                "target_dataset_name": "bad-adv",
            },
        )
        # Service raises ValueError → handler returns 400
        assert response.status_code == 400

    async def test_unknown_intent_returns_422(self, client: object) -> None:
        """The Pydantic schema's regex rejects unknown intents at
        validation time, before the handler runs."""
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/datasets/synthesize",
            json={
                "strategy": "adversarial",
                "adversarial_intent": "not_a_real_intent",
                "count": 5,
                "target_dataset_name": "bad-intent",
            },
        )
        assert response.status_code == 422


class TestSynthesisJobEndpoint:
    async def test_get_job_404_for_unknown(self, client: object) -> None:
        response = await client.get(  # type: ignore[union-attr]
            f"/api/evaluation/synthesis-jobs/{uuid.uuid4()}"
        )
        assert response.status_code == 404


class TestEvaluatorsListEndpoint:
    async def test_list_evaluators_returns_16(self, client: object) -> None:
        """Spec invariant: GET /api/evaluation/evaluators returns 16."""
        response = await client.get("/api/evaluation/evaluators")  # type: ignore[union-attr]
        assert response.status_code == 200
        data = response.json()
        # 16 - 4 ragas optional = 12 if ragas missing; but in this env
        # ragas is installed so should be 16
        assert data["total"] == 16
        names = {it["name"] for it in data["items"]}
        for expected in (
            "correctness",
            "contains",
            "refusal",
            "harmfulness",
            "pii_leakage",
            "tool_call_accuracy",
        ):
            assert expected in names

    async def test_filter_by_scope(self, client: object) -> None:
        response = await client.get(  # type: ignore[union-attr]
            "/api/evaluation/evaluators?scope=safety"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        for item in data["items"]:
            assert item["scope"] == "safety"
        names = {it["name"] for it in data["items"]}
        assert names == {"refusal", "harmfulness", "pii_leakage"}
