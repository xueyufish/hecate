"""Tests for publish evaluation gate + named dataset versions (7.3a + 7.3b).

Covers the dataset-version service (create / list / soft-delete / checkout /
diff), the shared snapshot module, run version binding, the engine
threshold persistence, and the publish gate (deterministic-only verdict,
LLM-judge immunity, force bypass, gate-by-mode semantics).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    RunStatus,
)
from hecate.ops.evaluation.dataset_version_service import (
    DatasetNotFoundError,
    DatasetVersionNameConflictError,
    DatasetVersionNotFoundError,
    EvaluationDatasetVersionService,
)
from hecate.ops.evaluation.engine import EvaluationEngine
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.publish_gate import (
    GateResult,
    GateSignalConfig,
    evaluate_gate,
    resolve_gate_config,
    result_to_report_payload,
    validate_gate_config,
)
from hecate.ops.evaluation.snapshot import build_snapshot, compute_content_hash
from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner
from hecate.ops.evaluation.types import EvalInput, EvalOutput, Score

USER_ID = uuid.uuid4()
WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StaticEvaluator(Evaluator):
    """Deterministic evaluator returning a fixed score for every item."""

    def __init__(self, value: float = 1.0, source: str = "deterministic", name: str = "static") -> None:
        self._value = value
        self._source = source
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "returns a fixed score"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        return EvalOutput(
            scores=[
                Score(
                    metric_name=self._name,
                    value=self._value,
                    source=self._source,
                )
            ]
        )


async def _seed_dataset(db_session, items: list[dict]) -> uuid.UUID:
    """Create a dataset with the given item dicts and return its id."""
    ds = EvaluationDatasetModel(name=f"ds-{uuid.uuid4()}", workspace_id=WORKSPACE)
    db_session.add(ds)
    await db_session.flush()
    for entry in items:
        item = EvaluationItemModel(
            dataset_id=ds.id,
            query=entry["query"],
            expected_answer=entry.get("expected_answer"),
            context=entry.get("context") or [],
            tags=list(entry.get("tags") or []),
            metadata_=entry.get("metadata", {}),
            known_bad=bool(entry.get("known_bad")),
            known_bad_reason=entry.get("known_bad_reason") if entry.get("known_bad") else None,
            workspace_id=WORKSPACE,
        )
        db_session.add(item)
    await db_session.flush()
    return ds.id


async def _seed_run(
    db_session,
    dataset_id: uuid.UUID,
    *,
    workflow_id: uuid.UUID | None = None,
    workflow_version: int | None = None,
    dataset_version_id: uuid.UUID | None = None,
    summary: dict | None = None,
):
    run = EvaluationRunModel(
        dataset_id=dataset_id,
        dataset_version_id=dataset_version_id,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        status=RunStatus.COMPLETED.value,
        summary=summary or {},
        repetitions=1,
        workspace_id=WORKSPACE,
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _seed_score(db_session, run_id: uuid.UUID, item_id: uuid.UUID, *, value: float, source: str):
    score = EvaluationScoreModel(
        run_id=run_id,
        item_id=item_id,
        metric_name="contains",
        value=value,
        source=source,
        workspace_id=WORKSPACE,
    )
    db_session.add(score)
    await db_session.flush()
    return score


# ---------------------------------------------------------------------------
# Shared snapshot module — same hash as the runner snapshot (D4)
# ---------------------------------------------------------------------------


class TestSharedSnapshotModule:
    async def test_version_hash_equals_runner_hash(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "expected_answer": "x"}, {"query": "b", "expected_answer": "y"}],
        )

        runner = OfflineTaskRunner(db_session)
        runner_snapshot, runner_hash = await runner._snapshot_dataset(dataset_id)

        svc = EvaluationDatasetVersionService(db_session)
        version = await svc.create_version(
            dataset_id,
            name="v1",
            description=None,
            workspace_id=WORKSPACE,
            created_by=USER_ID,
        )

        assert version.content_hash == runner_hash
        assert [entry["query"] for entry in version.items] == [entry["query"] for entry in runner_snapshot]

    async def test_marking_does_not_move_hash(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "expected_answer": "x"}],
        )
        runner = OfflineTaskRunner(db_session)
        before, hash_before = await runner._snapshot_dataset(dataset_id)

        # Mutate known_bad without touching content
        item = (
            await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id))
        ).scalar_one()
        item.known_bad = True
        item.known_bad_reason = "broken"
        item.known_bad_marked_by = USER_ID
        await db_session.flush()

        after, hash_after = await runner._snapshot_dataset(dataset_id)
        assert hash_after == hash_before
        # Marked entry carries the marker
        assert after[0].get("known_bad") is True
        # Content view is identical
        assert compute_content_hash(after) == compute_content_hash(before)


# ---------------------------------------------------------------------------
# Dataset version service — CRUD, naming, checkout, diff
# ---------------------------------------------------------------------------


class TestDatasetVersionService:
    async def test_create_free_live_items_with_hash(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "expected_answer": "x"}, {"query": "b"}],
        )
        svc = EvaluationDatasetVersionService(db_session)
        version = await svc.create_version(
            dataset_id,
            name="v1",
            description="baseline",
            workspace_id=WORKSPACE,
            created_by=USER_ID,
        )

        assert version.id is not None
        assert version.dataset_id == dataset_id
        assert version.name == "v1"
        assert len(version.items) == 2
        assert version.content_hash  # sha256
        assert len(version.content_hash) == 64
        assert version.created_by == USER_ID

    async def test_duplicate_name_rejected(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        svc = EvaluationDatasetVersionService(db_session)
        await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

        with pytest.raises(DatasetVersionNameConflictError):
            await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

    async def test_soft_deleted_name_stays_reserved(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        svc = EvaluationDatasetVersionService(db_session)
        v = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)
        await svc.delete_version(v.id, workspace_id=WORKSPACE)

        with pytest.raises(DatasetVersionNameConflictError):
            await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

    async def test_list_and_get(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        svc = EvaluationDatasetVersionService(db_session)
        v1 = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)
        await svc.create_version(dataset_id, "v2", None, WORKSPACE, USER_ID)

        versions, total = await svc.list_versions(dataset_id, workspace_id=WORKSPACE)
        assert total == 2
        assert {v.name for v in versions} == {"v1", "v2"}

        fetched = await svc.get_version(v1.id, workspace_id=WORKSPACE)
        assert fetched.id == v1.id

        with pytest.raises(DatasetVersionNotFoundError):
            await svc.get_version(uuid.uuid4(), workspace_id=WORKSPACE)

    async def test_unknown_dataset(self, db_session) -> None:
        svc = EvaluationDatasetVersionService(db_session)
        with pytest.raises(DatasetNotFoundError):
            await svc.create_version(uuid.uuid4(), "v1", None, WORKSPACE, USER_ID)

    async def test_checkout_replaces_live_items(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "expected_answer": "x"}, {"query": "b"}],
        )
        svc = EvaluationDatasetVersionService(db_session)
        version = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

        # Mutate the live dataset before checkout
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )
        items[0].query = "MUTATED"
        await db_session.flush()

        summary = await svc.checkout(dataset_id, version.id, workspace_id=WORKSPACE)
        assert summary["live_items_after"] == 2
        # Mutated item gone; original item restored with frozen content.
        live_after = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(
                        EvaluationItemModel.dataset_id == dataset_id, ~EvaluationItemModel.deleted
                    )
                )
            ).scalars()
        )
        queries = sorted(item.query for item in live_after)
        assert queries == ["a", "b"]

    async def test_checkout_preserves_known_bad_provenance(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "known_bad": True, "known_bad_reason": "stale"}],
        )
        svc = EvaluationDatasetVersionService(db_session)
        version = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

        # Reset the live dataset then checkout
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )
        for item in items:
            item.deleted = True
        await db_session.flush()

        await svc.checkout(dataset_id, version.id, workspace_id=WORKSPACE)
        restored = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(
                        EvaluationItemModel.dataset_id == dataset_id, ~EvaluationItemModel.deleted
                    )
                )
            ).scalars()
        )
        assert len(restored) == 1
        assert restored[0].known_bad is True
        assert restored[0].known_bad_reason == "stale"

    async def test_diff_against_live(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "expected_answer": "x"}, {"query": "b"}],
        )
        svc = EvaluationDatasetVersionService(db_session)
        version = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

        # Mutate live
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )
        items[0].expected_answer = "MUTATED"
        items[1].deleted = True
        new_item = EvaluationItemModel(
            dataset_id=dataset_id,
            query="c",
            workspace_id=WORKSPACE,
        )
        db_session.add(new_item)
        await db_session.flush()

        diff = await svc.diff(dataset_id, version.id, None, workspace_id=WORKSPACE)
        # "c" added; "b" removed; "a" changed on expected_answer
        added_queries = {entry["query"] for entry in diff["added"]}
        removed_queries = {entry["query"] for entry in diff["removed"]}
        # ``changed`` is a flat list of {item_id, fields} records
        changed_for_a = next(
            (change for change in diff["changed"] if change["item_id"] == str(items[0].id)),
            None,
        )
        assert added_queries == {"c"}
        assert removed_queries == {"b"}
        assert changed_for_a is not None
        assert "expected_answer" in changed_for_a["fields"]
        assert diff["base"]["name"] == "v1"
        assert diff["target"]["kind"] == "live"

    async def test_diff_against_version(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "expected_answer": "x"}, {"query": "b"}],
        )
        svc = EvaluationDatasetVersionService(db_session)
        v1 = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

        # Mutate live then snapshot a new version
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )
        items[0].expected_answer = "Y"
        await db_session.flush()
        v2 = await svc.create_version(dataset_id, "v2", None, WORKSPACE, USER_ID)

        diff = await svc.diff(dataset_id, v1.id, v2.id, workspace_id=WORKSPACE)
        assert len(diff["changed"]) == 1
        assert "expected_answer" in diff["changed"][0]["fields"]
        assert diff["target"]["name"] == "v2"


# ---------------------------------------------------------------------------
# Engine — summary threshold persistence + items override (run version binding)
# ---------------------------------------------------------------------------


class TestEngineSummaryThreshold:
    async def test_threshold_persisted_in_summary(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}, {"query": "b"}])
        run = await _seed_run(db_session, dataset_id, summary=None)
        engine = EvaluationEngine(db_session)
        await engine.run(
            evaluators=[_StaticEvaluator(value=0.9, name="contains")],
            dataset_id=dataset_id,
            answer_source=__import__("hecate.ops.evaluation.types", fromlist=["AnswerSource"]).AnswerSource.MANUAL,
            run=run,
            summary_config={"threshold": 0.8},
            repetitions=1,
        )

        assert (run.summary or {}).get("threshold") == 0.8

    async def test_no_threshold_omits_key(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        run = await _seed_run(db_session, dataset_id)
        engine = EvaluationEngine(db_session)
        await engine.run(
            evaluators=[_StaticEvaluator(value=0.9, name="contains")],
            dataset_id=dataset_id,
            answer_source=__import__("hecate.ops.evaluation.types", fromlist=["AnswerSource"]).AnswerSource.MANUAL,
            run=run,
            repetitions=1,
        )

        assert "threshold" not in (run.summary or {})


# ---------------------------------------------------------------------------
# Publish gate — deterministic-only verdict (D2/D3/D8)
# ---------------------------------------------------------------------------


class TestPublishGateConfig:
    def test_resolve_off(self) -> None:
        config = resolve_gate_config(None)
        assert config.mode == "off"

    def test_validate_require_needs_signal(self) -> None:
        with pytest.raises(ValueError):
            validate_gate_config({"mode": "require"})

    def test_require_with_signal_ok(self) -> None:
        validate_gate_config({"mode": "require", "require_run": True})

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_gate_config({"mode": "ignore"})


class TestPublishGateDeterministicOnly:
    async def test_min_pass_rate_only_uses_deterministic_scores(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a"}, {"query": "b"}, {"query": "c"}],
        )
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )

        candidate = await _seed_run(db_session, dataset_id, summary={"threshold": 0.5})
        # Two items score above deterministic threshold, one below; LLM judge
        # scores are below threshold but should be ignored by the gate.
        await _seed_score(db_session, candidate.id, items[0].id, value=0.9, source="deterministic")
        await _seed_score(db_session, candidate.id, items[1].id, value=0.7, source="deterministic")
        await _seed_score(db_session, candidate.id, items[2].id, value=0.1, source="deterministic")
        await _seed_score(db_session, candidate.id, items[0].id, value=0.1, source="llm_judge")
        await _seed_score(db_session, candidate.id, items[1].id, value=0.1, source="llm_judge")
        await _seed_score(db_session, candidate.id, items[2].id, value=0.1, source="llm_judge")

        config = resolve_gate_config({"mode": "require", "min_pass_rate": 0.7})
        result = await evaluate_gate(db_session, config, candidate, None, None)

        assert isinstance(result, GateResult)
        assert result.deterministic_pass_rate == pytest.approx(2 / 3)
        min_signal = next(s for s in result.signals if s.name == "min_pass_rate")
        assert min_signal.passed is False
        assert result.blocking is True

    async def test_require_run_signal(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        config = resolve_gate_config({"mode": "require", "require_run": True})

        result_missing = await evaluate_gate(db_session, config, None, None, None)
        require_run = next(s for s in result_missing.signals if s.name == "require_run")
        assert require_run.passed is False

        candidate = await _seed_run(db_session, dataset_id)
        result_present = await evaluate_gate(db_session, config, candidate, None, None)
        require_run_present = next(s for s in result_present.signals if s.name == "require_run")
        assert require_run_present.passed is True

    async def test_require_dataset_version_signal(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        config = resolve_gate_config({"mode": "require", "require_dataset_version": True})

        unbound = await _seed_run(db_session, dataset_id, dataset_version_id=None)
        result_unbound = await evaluate_gate(db_session, config, unbound, None, None)
        assert not next(s for s in result_unbound.signals if s.name == "require_dataset_version").passed

        bound_id = uuid.uuid4()
        bound = await _seed_run(db_session, dataset_id, dataset_version_id=bound_id)
        result_bound = await evaluate_gate(db_session, config, bound, None, None)
        assert next(s for s in result_bound.signals if s.name == "require_dataset_version").passed

    async def test_block_on_regression_ignores_llm_judge(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )

        baseline = await _seed_run(db_session, dataset_id, summary={"threshold": 0.5})
        await _seed_score(db_session, baseline.id, items[0].id, value=1.0, source="deterministic")
        await _seed_score(db_session, baseline.id, items[0].id, value=0.9, source="llm_judge")

        candidate = await _seed_run(db_session, dataset_id, summary={"threshold": 0.5})
        # Deterministic holds steady; LLM judge regresses hard.
        await _seed_score(db_session, candidate.id, items[0].id, value=1.0, source="deterministic")
        await _seed_score(db_session, candidate.id, items[0].id, value=0.1, source="llm_judge")

        config = resolve_gate_config({"mode": "require", "block_on_regression": True})
        result = await evaluate_gate(db_session, config, candidate, baseline, None)
        reg_signal = next(s for s in result.signals if s.name == "block_on_regression")
        assert reg_signal.passed is True
        assert reg_signal.detail.get("regressions", []) == []
        assert result.blocking is False

    async def test_no_deterministic_scores_fails_min_pass_rate(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )
        candidate = await _seed_run(db_session, dataset_id, summary={"threshold": 0.5})
        await _seed_score(db_session, candidate.id, items[0].id, value=0.9, source="llm_judge")

        config = resolve_gate_config({"mode": "require", "min_pass_rate": 0.5})
        result = await evaluate_gate(db_session, config, candidate, None, None)
        assert result.has_deterministic_scores is False
        min_signal = next(s for s in result.signals if s.name == "min_pass_rate")
        assert min_signal.passed is False
        assert min_signal.detail.get("reason") == "no_deterministic_scores"

    async def test_drift_signal(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        snapshot = build_snapshot(
            list(
                (
                    await db_session.execute(
                        select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                    )
                ).scalars()
            )
        )[1]

        candidate = await _seed_run(
            db_session,
            dataset_id,
            summary={"threshold": 0.5},
        )
        candidate.dataset_snapshot = {"hash": snapshot, "items": [{"id": "fake"}]}
        await db_session.flush()

        config = resolve_gate_config({"mode": "require", "block_on_drift": True})

        result_clean = await evaluate_gate(db_session, config, candidate, None, snapshot)
        drift = next(s for s in result_clean.signals if s.name == "block_on_drift")
        assert drift.passed is True

        result_drift = await evaluate_gate(db_session, config, candidate, None, "different-hash")
        drift_signal = next(s for s in result_drift.signals if s.name == "block_on_drift")
        assert drift_signal.passed is False
        assert drift_signal.detail["snapshot_hash"] == snapshot
        assert drift_signal.detail["current_hash"] == "different-hash"

    async def test_warn_mode_does_not_block(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        candidate = await _seed_run(db_session, dataset_id, summary={"threshold": 0.9})
        config = resolve_gate_config({"mode": "warn", "min_pass_rate": 0.95})

        result = await evaluate_gate(db_session, config, candidate, None, None)
        assert result.blocking is False
        assert result.enabled is True

    async def test_report_payload_round_trip(self) -> None:
        config = GateSignalConfig(
            mode="require",
            min_pass_rate=0.9,
            block_on_regression=True,
            block_on_drift=False,
            require_run=False,
            require_dataset_version=False,
        )
        result = GateResult(
            mode=config.mode,
            signals=[],
            deterministic_pass_rate=0.85,
            deterministic_metric_averages={"contains": 0.85},
            dataset_drift=None,
            has_deterministic_scores=True,
        )
        payload = result_to_report_payload(result, bypassed=False)
        assert payload["mode"] == "require"
        assert payload["bypassed_by_force"] is False
        assert payload["deterministic_pass_rate"] == 0.85
        assert payload["deterministic_metric_averages"] == {"contains": 0.85}


# ---------------------------------------------------------------------------
# Run version binding — engine uses items_override, no live drift detection
# ---------------------------------------------------------------------------


class TestRunVersionBinding:
    async def test_engine_runs_version_items_not_live(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "expected_answer": "x"}, {"query": "b"}],
        )
        svc = EvaluationDatasetVersionService(db_session)
        version = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

        # Mutate the live dataset after freezing the version
        items = list(
            (
                await db_session.execute(
                    select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)
                )
            ).scalars()
        )
        items[0].query = "MUTATED"
        items[0].deleted = True
        new_item = EvaluationItemModel(dataset_id=dataset_id, query="c", workspace_id=WORKSPACE)
        db_session.add(new_item)
        await db_session.flush()

        from hecate.ops.evaluation.types import AnswerSource

        run = await _seed_run(
            db_session,
            dataset_id,
            dataset_version_id=version.id,
            summary=None,
        )

        # Rehydrate frozen items and pass them through the engine.
        snapshot_items, _ = await runner_snapshot_via_version(svc, dataset_id, version.id)
        engine = EvaluationEngine(db_session)
        result = await engine.run(
            evaluators=[_StaticEvaluator(value=0.95, name="contains")],
            dataset_id=dataset_id,
            answer_source=AnswerSource.MANUAL,
            run=run,
            repetitions=1,
            items_override=snapshot_items,
        )

        # Only the version's two items execute — not "MUTATED" or "c".
        assert result.total_items == 2
        scored = (
            await db_session.execute(select(EvaluationScoreModel.item_id).where(EvaluationScoreModel.run_id == run.id))
        ).all()
        scored_ids = {str(row[0]) for row in scored}
        frozen_ids = {entry["id"] for entry in version.items}
        assert scored_ids == frozen_ids

    async def test_version_bound_run_does_not_report_drift(self, db_session) -> None:
        # 7.3b: the runner skips drift detection for version-bound runs.
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        svc = EvaluationDatasetVersionService(db_session)
        version = await svc.create_version(dataset_id, "v1", None, WORKSPACE, USER_ID)

        run = await _seed_run(db_session, dataset_id, dataset_version_id=version.id)
        runner = OfflineTaskRunner(db_session)
        await runner._append_dataset_drift(run, db_session)

        assert (run.summary or {}).get("dataset_drift") is None


async def runner_snapshot_via_version(svc, dataset_id, version_id) -> tuple[list, str]:
    version = await svc.get_version(version_id, workspace_id=WORKSPACE)
    from hecate.ops.evaluation.tasks.runner import _items_from_snapshot

    items = _items_from_snapshot(version.items or [], dataset_id)
    return items, str(version.content_hash)
