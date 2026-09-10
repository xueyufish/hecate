"""End-to-end tests for DatasetSynthesisService against a real db_session."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.ops.evaluation.synthesis import DatasetSynthesisService
from hecate.ops.evaluation.synthesis.service import SynthesisRequest


def _patch_quality_all_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force QualityFilter critic to always return 1.0/1.0.

    A single chat fixture shared with strategy: cycle so the first
    :pyattr:`strategy_calls` invocations return the strategy payload
    and subsequent ones return the quality payload.
    """

    quality = json.dumps({"self_containment": 1.0, "clarity": 1.0})

    async def _chat(*_a: object, **_kw: object) -> object:
        class _R:
            content = quality

        return _R

    import hecate_llm.service

    monkeypatch.setattr(hecate_llm.service.llm_service, "chat", _chat)


def _patch_llm_payload(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    async def _chat(*_a: object, **_kw: object) -> object:
        class _R:
            content = json.dumps(payload)

        return _R

    # Import the module so monkeypatch.setattr resolves the dotted path
    # through the module attribute, not by string lookup.
    import hecate_llm.service

    monkeypatch.setattr(hecate_llm.service.llm_service, "chat", _chat)


class TestSynchronousSynthesis:
    @pytest.mark.asyncio
    async def test_generation_strategy_small_batch(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """count=10 with topic — generation strategy produces ~10 items."""
        # Patch chat with a cycle: first call returns the strategy payload
        # (used by GenerationStrategy), all subsequent calls return the
        # quality payload (used by QualityFilter critic).
        from hecate_llm.service import llm_service

        strategy_payload = json.dumps({"query": "What is X?", "expected_answer": "X is a thing"})
        quality_payload = json.dumps({"self_containment": 1.0, "clarity": 1.0})
        counter: dict[str, int] = {"n": 0}

        async def _chat(*_a: object, **_kw: object) -> object:
            counter["n"] += 1
            payload = strategy_payload if counter["n"] <= 10 else quality_payload

            class _R:
                content = payload

            return _R

        monkeypatch.setattr(llm_service, "chat", _chat)

        # Disable DLP by patching the service class to no-op
        from hecate.ops.dlp import service as dlp_mod

        class _StubDLP:
            def __init__(self, *_a: object, **_kw: object) -> None:
                pass

            async def dry_run_scan(self, *_a: object, **_kw: object) -> object:
                class _R:
                    findings: list = []

                return _R()

        monkeypatch.setattr(dlp_mod, "DLPService", _StubDLP)

        svc = DatasetSynthesisService(db_session)
        req = SynthesisRequest(
            seed_dataset_id=None,
            topic="enterprise SSO troubleshooting",
            strategy="generation",
            adversarial_intent=None,
            count=10,
            target_dataset_name="gen-test-sso",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=0.5,
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        )
        result = await svc.synthesize(req)
        assert result.items_generated == 10
        assert result.items_filtered == 0
        assert result.target_dataset_id is not None

        # Items were persisted with tags
        from sqlalchemy import select

        from hecate.models.evaluation import EvaluationItemModel

        stmt = select(EvaluationItemModel).where(
            EvaluationItemModel.dataset_id == result.target_dataset_id,
        )
        items = (await db_session.execute(stmt)).scalars().all()
        assert len(items) == 10
        for it in items:
            assert "synthetic" in (it.tags or [])
            assert "strategy:generation" in (it.tags or [])

    @pytest.mark.asyncio
    async def test_adversarial_requires_intent(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_llm_payload(monkeypatch, {"query": "q", "expected_answer": "a"})
        svc = DatasetSynthesisService(db_session)
        req = SynthesisRequest(
            seed_dataset_id=None,
            topic=None,
            strategy="adversarial",
            adversarial_intent=None,  # missing!
            count=2,
            target_dataset_name="bad",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=0.5,
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        )
        with pytest.raises(ValueError, match="adversarial_intent"):
            await svc.synthesize(req)

    @pytest.mark.asyncio
    async def test_non_adversarial_requires_seed_or_topic(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        svc = DatasetSynthesisService(db_session)
        req = SynthesisRequest(
            seed_dataset_id=None,
            topic=None,
            strategy="generation",
            adversarial_intent=None,
            count=1,
            target_dataset_name="bad",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=0.5,
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        )
        with pytest.raises(ValueError, match="seed_dataset_id"):
            await svc.synthesize(req)


class TestDatasetSchemaTags:
    @pytest.mark.asyncio
    async def test_tags_round_trip_via_export(
        self,
        db_session: AsyncSession,
    ) -> None:
        from hecate.ops.evaluation.dataset_service import EvaluationDatasetService

        svc = EvaluationDatasetService(db_session)
        ds = await svc.create_dataset(name="tags-rt")
        await svc.add_items(
            ds.id,
            [
                {"query": "q1", "tags": ["a", "b"]},
                {"query": "q2", "tags": []},
                {"query": "q3", "tags": ["c"]},
            ],
        )
        await db_session.flush()
        exported = await svc.export_json(ds.id)
        assert exported[0]["tags"] == ["a", "b"]
        assert exported[1]["tags"] == []
        assert exported[2]["tags"] == ["c"]

    @pytest.mark.asyncio
    async def test_list_items_filter_by_tag(
        self,
        db_session: AsyncSession,
    ) -> None:
        from hecate.ops.evaluation.dataset_service import EvaluationDatasetService

        svc = EvaluationDatasetService(db_session)
        ds = await svc.create_dataset(name="tag-filter")
        await svc.add_items(
            ds.id,
            [
                {"query": "q1", "tags": ["intent:prompt_injection_basic"]},
                {"query": "q2", "tags": ["strategy:adversarial"]},
                {"query": "q3", "tags": ["intent:prompt_injection_basic", "strategy:adversarial"]},
            ],
        )
        await db_session.flush()
        items, _ = await svc.list_items(ds.id, tags=["intent:prompt_injection_basic"])
        assert {it.query for it in items} == {"q1", "q3"}

        items, _ = await svc.list_items(ds.id, tags=["strategy:adversarial"])
        assert {it.query for it in items} == {"q2", "q3"}

        items, _ = await svc.list_items(ds.id, tags=["nonexistent_tag"])
        assert items == []


class TestEvaluatorsListMetadata:
    """Spec invariant: list_evaluators returns 16 (12 always + 4 ragas-optional)."""

    def test_metadata_table_has_16_entries(self) -> None:
        from hecate.ops.api.evaluation import _EVALUATOR_SCOPE_META

        assert len(_EVALUATOR_SCOPE_META) == 16

        by_scope: dict[str, int] = {}
        for _name, (scope, _source) in _EVALUATOR_SCOPE_META.items():
            by_scope[scope] = by_scope.get(scope, 0) + 1
        # Spec taxonomy: result=7, process=2, rag=4, safety=3 → 16
        assert by_scope["result"] == 7
        assert by_scope["process"] == 2
        assert by_scope["rag"] == 4
        assert by_scope["safety"] == 3
