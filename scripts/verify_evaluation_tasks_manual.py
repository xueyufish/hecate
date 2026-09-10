"""Manual chain verification for 7.2c against a real PostgreSQL (tasks.md 5.2).

Run with DATABASE_URL pointing at the throwaway verification PG:
  DATABASE_URL=postgresql+asyncpg://hecate:hecate@localhost:5433/hecate \
    .venv/bin/python scripts/verify_evaluation_tasks_manual.py

Chains: dataset+items → offline task → trigger → runner execute → run
completed with summary → online task (sampling_rate=1.0) → worker cycle over
a fabricated completed trace → score rows queryable → disable → no new
scores. No LLM calls: only the deterministic `contains` evaluator is used.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

# Settings must point at the verification DB before importing core.database.
from hecate.core.config import settings

settings.DATABASE_URL = "postgresql+asyncpg://hecate:hecate@localhost:5433/hecate"

import hecate.core.database as database_mod  # noqa: E402
from hecate.models.evaluation import (  # noqa: E402
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationTaskModel,
    RunStatus,
    TaskType,
)
from hecate.models.session import SessionModel  # noqa: E402
from hecate.models.trace import TraceModel  # noqa: E402
from hecate.ops.evaluation.dataset_service import EvaluationDatasetService  # noqa: E402
from hecate.ops.evaluation.engine import register_evaluators  # noqa: E402
from hecate.ops.evaluation.tasks.online_worker import OnlineEvaluationWorker  # noqa: E402
from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner  # noqa: E402
from hecate.ops.evaluation.tasks.service import EvaluationTaskService  # noqa: E402
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore  # noqa: E402

WS = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
AGENT = uuid.UUID("00000000-0000-0000-0000-0000000000e2")
NOW = datetime.now(UTC)


async def main() -> None:
    factory = database_mod.async_session_factory
    assert factory is not None

    # register_evaluators only needs an object with .register(manifest, instance)
    class _Registry:
        def register(self, manifest, instance) -> None:
            pass

    # Online scoring judges free-form conversations — in production that is
    # an LLM-judge (e.g. `relevancy`); this verification registers a minimal
    # deterministic stand-in through the same class index the composition
    # root populates, so no LLM is called.
    import hecate.ops.evaluation.engine as engine_mod
    from hecate.ops.evaluation.evaluator import Evaluator
    from hecate.ops.evaluation.types import EvalInput as EvalInputAlias
    from hecate.ops.evaluation.types import EvalOutput as EvalOutputAlias
    from hecate.ops.evaluation.types import Score as ScoreAlias

    class _KeywordParis(Evaluator):
        @property
        def name(self) -> str:
            return "keyword_paris"

        @property
        def description(self) -> str:
            return "1.0 when the answer mentions Paris"

        async def evaluate(self, input: EvalInputAlias) -> EvalOutputAlias:  # noqa: A002 — ABC signature
            value = 1.0 if "paris" in (input.generated_answer or "").lower() else 0.0
            return EvalOutputAlias(scores=[ScoreAlias(metric_name=self.name, value=value, source="deterministic")])

    engine_mod._EVALUATOR_CLASS_REGISTRY["keyword_paris"] = _KeywordParis
    register_evaluators(_Registry())

    async with factory() as db:
        # --- Offline chain -------------------------------------------------
        ds_svc = EvaluationDatasetService(db)
        dataset = await ds_svc.create_dataset(name="verify-manual", workspace_id=WS)
        # add_items does not persist generated_answer (7.2 contract: answers
        # are filled by agent execution or at evaluation time), so pre-bake
        # them directly for the manual answer_source.
        await ds_svc.add_items(
            dataset.id,
            [
                {"query": "capital of France", "expected_answer": "Paris"},
                {"query": "capital of Japan", "expected_answer": "Tokyo"},
            ],
        )
        from sqlalchemy import update

        await db.execute(
            update(EvaluationItemModel)
            .where(EvaluationItemModel.dataset_id == dataset.id, EvaluationItemModel.query == "capital of France")
            .values(generated_answer="Paris is the capital of France.")
        )
        # Second item intentionally left unanswered → should fail threshold.

        svc = EvaluationTaskService(db)

        class TaskIn:
            task_type = TaskType.OFFLINE.value
            name = "verify-offline"
            description = None
            evaluators = ["contains"]
            dataset_id = dataset.id
            answer_source = "manual"
            threshold = 0.5
            baseline_run_id = None
            regression_threshold = None
            tags = None
            agent_id = None
            sampling_rate = None
            max_traces_per_cycle = None

        task = await svc.create_task(TaskIn(), workspace_id=WS)
        run = await svc.trigger_run(task.id, workspace_id=WS)
        await db.commit()

        runner = OfflineTaskRunner(db)
        await runner._execute(run.id, task.id)

        # Read through a fresh session — the outer session caches instances
        # (expire_on_commit=False) and would show a stale status.
        async with factory() as verify_session:
            completed = await verify_session.get(EvaluationRunModel, run.id)
            assert completed.status == RunStatus.COMPLETED.value, completed.status
            assert completed.summary["total_items"] == 2
            assert completed.summary["passed_items"] == 1, completed.summary
            assert completed.summary["failed_items"] == 1
            print(f"[offline] run={completed.status} summary={completed.summary}")

        # --- Online chain ---------------------------------------------------
        session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=AGENT)
        db.add(session_row)
        await db.flush()
        trace = TraceModel(
            trace_id=uuid.uuid4(),
            type="trace",
            name=f"session:{session_row.id}",
            session_id=session_row.id,
            status="completed",
            start_time=NOW,
            end_time=NOW + timedelta(seconds=3),
            created_at=NOW,
        )
        db.add(trace)
        await db.commit()

        store = InMemoryEventStore()
        await store.append(
            Event(
                session_id=session_row.id,
                superstep=1,
                event_type=EventType.LLM_REQUEST,
                timestamp=NOW + timedelta(seconds=1),
                payload={"messages": [{"role": "user", "content": "capital of France?"}]},
            )
        )
        await store.append(
            Event(
                session_id=session_row.id,
                superstep=1,
                event_type=EventType.LLM_REQUEST,
                timestamp=NOW + timedelta(seconds=2),
                payload={"messages": [{"role": "assistant", "content": "Paris, obviously."}]},
            )
        )

        class OnlineIn:
            task_type = TaskType.ONLINE.value
            name = "verify-online"
            description = None
            evaluators = ["keyword_paris"]
            dataset_id = None
            answer_source = None
            threshold = None
            baseline_run_id = None
            regression_threshold = None
            tags = None
            agent_id = AGENT
            sampling_rate = 1.0
            max_traces_per_cycle = 10

        online_task = await svc.create_task(OnlineIn(), workspace_id=WS)
        await db.commit()

        worker = OnlineEvaluationWorker(event_store=store)
        await worker._process_task(online_task)

        scores, total = await svc.list_scores(workspace_id=WS, task_id=online_task.id)
        assert total == 1, f"expected 1 score, got {total}"
        score = scores[0]
        assert score.value == 1.0, (score.value, score.reasoning)
        assert score.target_id == trace.id and score.agent_id == AGENT
        print(f"[online] score={score.metric_name}={score.value} target={score.target_id}")

        # Rescan idempotency: rewind watermark, expect no duplicates.
        async with factory() as s2:
            refreshed = await s2.get(EvaluationTaskModel, online_task.id)
            refreshed.last_scanned_at = None
            await s2.commit()
        await worker._process_task(online_task)
        _, total_after = await svc.list_scores(workspace_id=WS, task_id=online_task.id)
        assert total_after == 1, f"idempotency broken: {total_after}"
        print("[online] rescan dedupe OK")

        # Disable → cycle must not produce anything further.
        disabled = await svc.disable_task(online_task.id, workspace_id=WS)
        assert disabled.status == "disabled"
        await worker._process_task(disabled)
        _, total_disabled = await svc.list_scores(workspace_id=WS, task_id=online_task.id)
        assert total_disabled == 1
        print("[online] disable stops scoring OK")

    print("MANUAL VERIFICATION PASSED")


if __name__ == "__main__":
    asyncio.run(main())
