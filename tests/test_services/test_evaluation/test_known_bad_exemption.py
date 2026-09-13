"""Tests for known-bad exemption labeling (7.3c).

Covers: item marking + provenance (dataset service), snapshot sparse
serialization + hash exclusion (offline runner), aggregation exclusion
+ healed signal (engine), regression averages (engine), and the HTTP
marking API.
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
from hecate.ops.evaluation.dataset_service import EvaluationDatasetService
from hecate.ops.evaluation.engine import EvaluationEngine
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner
from hecate.ops.evaluation.types import EvalInput, EvalOutput, Score

MARKER = uuid.uuid4()


class _StaticEvaluator(Evaluator):
    """Deterministic evaluator returning a fixed score for every item."""

    def __init__(self, value: float = 1.0) -> None:
        self._value = value

    @property
    def name(self) -> str:
        return "static"

    @property
    def description(self) -> str:
        return "returns a fixed score"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        return EvalOutput(scores=[Score(metric_name="static", value=self._value, source="deterministic")])


class _QueryKeyedEvaluator(Evaluator):
    """Returns a configured score per query; unmapped queries score 0.0."""

    def __init__(self, values: dict[str, float]) -> None:
        self._values = values

    @property
    def name(self) -> str:
        return "static"

    @property
    def description(self) -> str:
        return "returns per-query scores"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        value = self._values.get(input.query, 0.0)
        return EvalOutput(scores=[Score(metric_name="static", value=value, source="deterministic")])


async def _seed_dataset(db_session, items: list[dict]) -> uuid.UUID:
    dataset_id = uuid.uuid4()
    db_session.add(EvaluationDatasetModel(id=dataset_id, name="kb-test"))
    for item in items:
        db_session.add(EvaluationItemModel(dataset_id=dataset_id, **item))
    await db_session.flush()
    return dataset_id


class TestMarkingService:
    async def test_mark_sets_server_provenance(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "q1"}])
        item = (
            await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id))
        ).scalar_one()

        svc = EvaluationDatasetService(db_session)
        updated = await svc.update_item(item.id, known_bad=True, known_bad_reason="stale fixture", marked_by=MARKER)

        assert updated.known_bad is True
        assert updated.known_bad_reason == "stale fixture"
        assert updated.known_bad_marked_by == MARKER
        assert updated.known_bad_marked_at is not None

    async def test_mark_without_reason_rejected_and_item_unchanged(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "q1"}])
        item = (
            await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id))
        ).scalar_one()

        svc = EvaluationDatasetService(db_session)
        with pytest.raises(ValueError, match="reason"):
            await svc.update_item(item.id, known_bad=True, known_bad_reason="  ", marked_by=MARKER)

        await db_session.refresh(item)
        assert item.known_bad is False
        assert item.known_bad_marked_by is None

    async def test_clear_resets_all_markers(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "q1"}])
        item = (
            await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id))
        ).scalar_one()
        svc = EvaluationDatasetService(db_session)
        await svc.update_item(
            item.id,
            known_bad=True,
            known_bad_reason="stale",
            known_bad_expires_at=None,
            marked_by=MARKER,
        )

        cleared = await svc.update_item(item.id, known_bad=False, marked_by=MARKER)

        assert cleared.known_bad is False
        assert cleared.known_bad_reason is None
        assert cleared.known_bad_marked_by is None
        assert cleared.known_bad_marked_at is None
        assert cleared.known_bad_expires_at is None

    async def test_list_filter_by_exemption_status(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a"}, {"query": "b"}, {"query": "c"}],
        )
        items = (
            (await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id)))
            .scalars()
            .all()
        )
        svc = EvaluationDatasetService(db_session)
        await svc.update_item(items[0].id, known_bad=True, known_bad_reason="bad", marked_by=MARKER)

        marked, marked_total = await svc.list_items(dataset_id, known_bad=True)
        active, active_total = await svc.list_items(dataset_id, known_bad=False)
        everyone, everyone_total = await svc.list_items(dataset_id)

        assert [it.id for it in marked] == [items[0].id]
        assert marked_total == 1
        assert {it.id for it in active} == {items[1].id, items[2].id}
        assert active_total == 2
        assert everyone_total == 3

    async def test_update_unknown_item_raises(self, db_session) -> None:
        svc = EvaluationDatasetService(db_session)
        with pytest.raises(ValueError, match="not found"):
            await svc.update_item(uuid.uuid4(), known_bad=True, known_bad_reason="x", marked_by=MARKER)


class TestImportExportRoundTrip:
    async def test_marker_fields_round_trip(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "kept"}, {"query": "broken", "known_bad": True, "known_bad_reason": "stale"}],
        )
        # Fill provenance the way the marking service would.
        item = (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == dataset_id,
                    EvaluationItemModel.query == "broken",
                )
            )
        ).scalar_one()
        item.known_bad_marked_by = MARKER
        item.known_bad_marked_at = item.created_at
        await db_session.flush()

        svc = EvaluationDatasetService(db_session)
        exported = await svc.export_json(dataset_id)

        target = uuid.uuid4()
        db_session.add(EvaluationDatasetModel(id=target, name="kb-import"))
        result = await svc.import_json(target, exported)
        assert result["valid"] == 2

        reimported = {
            it.query: it
            for it in (
                await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == target))
            ).scalars()
        }
        assert reimported["kept"].known_bad is False
        assert reimported["broken"].known_bad is True
        assert reimported["broken"].known_bad_reason == "stale"
        assert reimported["broken"].known_bad_marked_by == MARKER
        assert reimported["broken"].known_bad_marked_at is not None

    async def test_legacy_import_without_markers_imports_unmarked(self, db_session) -> None:
        target = uuid.uuid4()
        db_session.add(EvaluationDatasetModel(id=target, name="legacy"))
        svc = EvaluationDatasetService(db_session)
        result = await svc.import_json(target, [{"query": "old-format"}])

        assert result["valid"] == 1
        item = (
            await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == target))
        ).scalar_one()
        assert item.known_bad is False
        assert item.known_bad_reason is None


class TestSnapshotExemption:
    async def test_sparse_serialization_and_hash_ignores_marking(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "a", "known_bad": True, "known_bad_reason": "stale"}, {"query": "b"}],
        )
        runner = OfflineTaskRunner(db_session)

        snapshot, digest = await runner._snapshot_dataset(dataset_id)

        by_query = {entry["query"]: entry for entry in snapshot}
        assert by_query["a"]["known_bad"] is True
        assert by_query["a"]["known_bad_reason"] == "stale"
        # Unmarked items serialize without any marker keys (byte-compatible
        # with the pre-7.3c snapshot format).
        assert not any(key.startswith("known_bad") for key in by_query["b"])

        # Marking the previously unmarked item must not move the hash.
        svc = EvaluationDatasetService(db_session)
        item_b = (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == dataset_id,
                    EvaluationItemModel.query == "b",
                )
            )
        ).scalar_one()
        await svc.update_item(item_b.id, known_bad=True, known_bad_reason="newly broken", marked_by=MARKER)

        snapshot_after, digest_after = await runner._snapshot_dataset(dataset_id)

        assert digest_after == digest
        assert {e["query"]: e for e in snapshot_after}["b"]["known_bad"] is True

    async def test_marking_after_snapshot_does_not_fire_drift(self, db_session) -> None:
        dataset_id = await _seed_dataset(db_session, [{"query": "a"}])
        runner = OfflineTaskRunner(db_session)
        items, digest = await runner._snapshot_dataset(dataset_id)

        run = EvaluationRunModel(
            dataset_id=dataset_id,
            status=RunStatus.COMPLETED.value,
            dataset_snapshot={"items": items, "hash": digest, "captured_at": "2026-09-12T00:00:00Z"},
        )
        db_session.add(run)
        await db_session.flush()

        # Mark the item AFTER the snapshot was captured (CLI exit-2 path
        # must stay silent: exemption is metadata, not dataset drift).
        svc = EvaluationDatasetService(db_session)
        item = (
            await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id))
        ).scalar_one()
        await svc.update_item(item.id, known_bad=True, known_bad_reason="stale", marked_by=MARKER)

        await runner._append_dataset_drift(run, db_session)

        assert (run.summary or {}).get("dataset_drift") is None


class TestAggregationExemption:
    async def test_exempted_items_removed_from_pass_rate_denominator(self, db_session) -> None:
        # 10 items, 2 known-bad, 6 of the 8 active items pass → 0.75.
        values = {f"q{i}": (1.0 if i < 6 else 0.0) for i in range(8)}
        items = [
            {"query": f"q{i}", "generated_answer": "a", **({"known_bad": True} if i in (8, 9) else {})}
            for i in range(10)
        ]
        dataset_id = await _seed_dataset(db_session, items)

        engine = EvaluationEngine(db_session)
        run_row = EvaluationRunModel(dataset_id=dataset_id, status=RunStatus.PENDING.value)
        db_session.add(run_row)
        await db_session.flush()
        await engine.run(
            [_QueryKeyedEvaluator(values)],
            dataset_id,
            run=run_row,
            summary_config={"threshold": 0.5},
        )

        summary = run_row.summary
        assert summary["total_items"] == 10
        assert summary["exempted_items"] == 2
        assert summary["passed_items"] == 6
        assert summary["failed_items"] == 2
        assert summary["pass_rate"] == pytest.approx(0.75)
        # repetitions == 1 → no consistency_rate, but exemption counts exist.
        assert "consistency_rate" not in summary

        # All 10 items (including exempted) still executed and recorded scores.
        score_rows = (
            (await db_session.execute(select(EvaluationScoreModel).where(EvaluationScoreModel.run_id == run_row.id)))
            .scalars()
            .all()
        )
        assert len(score_rows) == 10

    async def test_healed_exempted_item_reported_not_cleared(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "ok", "generated_answer": "a"}, {"query": "healed", "known_bad": True}],
        )
        marked_id = (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == dataset_id,
                    EvaluationItemModel.query == "healed",
                )
            )
        ).scalar_one()

        engine = EvaluationEngine(db_session)
        run_row = EvaluationRunModel(dataset_id=dataset_id, status=RunStatus.PENDING.value)
        db_session.add(run_row)
        await db_session.flush()
        await engine.run(
            [_StaticEvaluator(1.0)],
            dataset_id,
            run=run_row,
            summary_config={"threshold": 0.5},
        )

        assert run_row.summary["pass_rate"] == pytest.approx(1.0)  # 1 of 1 active
        assert run_row.summary["exempted_items"] == 1
        assert run_row.summary["known_bad_passed_item_ids"] == [str(marked_id.id)]

        await db_session.refresh(marked_id)
        assert marked_id.known_bad is True  # reported, never auto-cleared

    async def test_post_run_marking_not_retroactive(self, db_session) -> None:
        # First run: both items counted, the failing one drags pass_rate to 0.5.
        dataset_id = await _seed_dataset(
            db_session,
            [{"query": "good", "generated_answer": "a"}, {"query": "bad", "generated_answer": "b"}],
        )
        engine = EvaluationEngine(db_session)
        await engine.run(
            [_QueryKeyedEvaluator({"good": 1.0, "bad": 0.0})],
            dataset_id,
            summary_config={"threshold": 0.5},
        )
        first_run = (
            await db_session.execute(select(EvaluationRunModel).where(EvaluationRunModel.dataset_id == dataset_id))
        ).scalar_one()
        assert first_run.summary["pass_rate"] == pytest.approx(0.5)
        assert first_run.summary["exempted_items"] == 0

        # Mark the failing item known-bad; the FIRST summary stays frozen.
        bad_item = (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == dataset_id,
                    EvaluationItemModel.query == "bad",
                )
            )
        ).scalar_one()
        svc = EvaluationDatasetService(db_session)
        await svc.update_item(bad_item.id, known_bad=True, known_bad_reason="bad fixture", marked_by=MARKER)
        assert first_run.summary["pass_rate"] == pytest.approx(0.5)

        # Second run excludes it: pass_rate 1.0 (1 of 1 active), exempted 1.
        second = EvaluationRunModel(dataset_id=dataset_id, status=RunStatus.PENDING.value)
        db_session.add(second)
        await db_session.flush()
        await engine.run(
            [_QueryKeyedEvaluator({"good": 1.0, "bad": 0.0})],
            dataset_id,
            run=second,
            summary_config={"threshold": 0.5},
        )
        assert second.summary["pass_rate"] == pytest.approx(1.0)
        assert second.summary["exempted_items"] == 1


class TestRegressionExemption:
    async def test_regression_averages_exclude_known_bad_both_sides(self, db_session) -> None:
        dataset_id = await _seed_dataset(
            db_session,
            [
                {"query": "good", "generated_answer": "a"},
                {"query": "bad", "generated_answer": "b", "known_bad": True},
            ],
        )
        bad_item = (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == dataset_id,
                    EvaluationItemModel.query == "bad",
                )
            )
        ).scalar_one()
        good_item = (
            await db_session.execute(
                select(EvaluationItemModel).where(
                    EvaluationItemModel.dataset_id == dataset_id,
                    EvaluationItemModel.query == "good",
                )
            )
        ).scalar_one()

        # Baseline run with a frozen snapshot marking "bad" as exempt; its
        # scores would average 0.95 with the bad item included.
        baseline = EvaluationRunModel(
            dataset_id=dataset_id,
            status=RunStatus.COMPLETED.value,
            dataset_snapshot={
                "items": [
                    {"id": str(good_item.id), "query": "good"},
                    {"id": str(bad_item.id), "query": "bad", "known_bad": True},
                ],
                "hash": "baseline-hash",
            },
        )
        db_session.add(baseline)
        await db_session.flush()
        db_session.add(
            EvaluationScoreModel(
                run_id=baseline.id, item_id=good_item.id, metric_name="static", value=1.0, source="deterministic"
            )
        )
        db_session.add(
            EvaluationScoreModel(
                run_id=baseline.id, item_id=bad_item.id, metric_name="static", value=0.9, source="deterministic"
            )
        )
        await db_session.flush()

        # Candidate: active item scores 1.0, the known-bad item drops to 0.0.
        # Without exclusion candidate avg = 0.5 < baseline 0.95 → false regression.
        engine = EvaluationEngine(db_session)
        candidate = EvaluationRunModel(dataset_id=dataset_id, status=RunStatus.PENDING.value)
        db_session.add(candidate)
        await db_session.flush()
        result = await engine.run(
            [_QueryKeyedEvaluator({"good": 1.0, "bad": 0.0})],
            dataset_id,
            run=candidate,
            summary_config={"threshold": 0.5, "baseline_run_id": str(baseline.id)},
        )

        assert result.metric_averages["static"] == pytest.approx(1.0)
        # Empty regressions → the CLI three-state exit path stays at 0/2.
        assert candidate.summary["regressions"] == []


class TestExemptionAPI:
    async def test_mark_clear_and_read_round_trip(self, client: object) -> None:
        ds = (await client.post("/api/evaluation/datasets", json={"name": "kb-api"})).json()  # type: ignore[union-attr]
        await client.post(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items",
            json=[{"query": "q1"}],
        )
        list_resp = await client.get(f"/api/evaluation/datasets/{ds['id']}/items")  # type: ignore[union-attr]
        item_id = list_resp.json()["items"][0]["id"]

        mark_resp = await client.patch(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items/{item_id}",
            json={"known_bad": True, "known_bad_reason": "stale fixture"},
        )
        assert mark_resp.status_code == 200
        marked = mark_resp.json()
        assert marked["known_bad"] is True
        assert marked["known_bad_reason"] == "stale fixture"
        assert marked["known_bad_marked_by"]
        assert marked["known_bad_marked_at"]

        clear_resp = await client.patch(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items/{item_id}",
            json={"known_bad": False},
        )
        assert clear_resp.status_code == 200
        cleared = clear_resp.json()
        assert cleared["known_bad"] is False
        assert cleared["known_bad_reason"] is None
        assert cleared["known_bad_marked_by"] is None
        assert cleared["known_bad_marked_at"] is None

    async def test_mark_without_reason_returns_422(self, client: object) -> None:
        ds = (await client.post("/api/evaluation/datasets", json={"name": "kb-api-422"})).json()  # type: ignore[union-attr]
        await client.post(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items",
            json=[{"query": "q1"}],
        )
        item_id = (
            await client.get(f"/api/evaluation/datasets/{ds['id']}/items")  # type: ignore[union-attr]
        ).json()["items"][0]["id"]

        resp = await client.patch(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items/{item_id}",
            json={"known_bad": True},
        )
        assert resp.status_code == 422

        after = await client.get(f"/api/evaluation/datasets/{ds['id']}/items")  # type: ignore[union-attr]
        assert after.json()["items"][0]["known_bad"] is False

    async def test_patch_unknown_item_returns_404(self, client: object) -> None:
        ds = (await client.post("/api/evaluation/datasets", json={"name": "kb-api-404"})).json()  # type: ignore[union-attr]
        resp = await client.patch(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items/{uuid.uuid4()}",
            json={"known_bad": True, "known_bad_reason": "x"},
        )
        assert resp.status_code == 404

    async def test_list_known_bad_filter(self, client: object) -> None:
        ds = (await client.post("/api/evaluation/datasets", json={"name": "kb-api-filter"})).json()  # type: ignore[union-attr]
        await client.post(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items",
            json=[{"query": "q1"}, {"query": "q2"}],
        )
        item_id = (
            await client.get(f"/api/evaluation/datasets/{ds['id']}/items")  # type: ignore[union-attr]
        ).json()["items"][0]["id"]
        await client.patch(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items/{item_id}",
            json={"known_bad": True, "known_bad_reason": "stale"},
        )

        marked = (
            await client.get(  # type: ignore[union-attr]
                f"/api/evaluation/datasets/{ds['id']}/items",
                params={"known_bad": "true"},
            )
        ).json()
        active = (
            await client.get(  # type: ignore[union-attr]
                f"/api/evaluation/datasets/{ds['id']}/items",
                params={"known_bad": "false"},
            )
        ).json()

        assert marked["total"] == 1
        assert len(marked["items"]) == 1
        assert active["total"] == 1

    async def test_create_rejects_marker_fields(self, client: object) -> None:
        ds = (await client.post("/api/evaluation/datasets", json={"name": "kb-api-create"})).json()  # type: ignore[union-attr]
        resp = await client.post(  # type: ignore[union-attr]
            f"/api/evaluation/datasets/{ds['id']}/items",
            json=[{"query": "q1", "known_bad": True}],
        )
        # Create is always unmarked — marking goes through the update API
        # so provenance is server-recorded.
        assert resp.status_code == 422
