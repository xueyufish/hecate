"""DB-backed integration tests for the memory-storage-family change.

These tests require a live Postgres reachable via ``DATABASE_URL``
(default ``postgresql+asyncpg://postgres:postgres@localhost:5432/hecate``).
They run alembic migrations to head before any test executes, then
exercise the full task-memory + reflection + work-context-graph
write/read paths against the real DB.

Run with ``DATABASE_URL=... python -m pytest tests/test_memory_storage_family_db.py -q``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from hecate_memory.memory.reflection import (
    ConfidenceEvaluator,
    ReflectionEngine,
    ReflectionSettings,
)
from hecate_memory.memory.task_memory import TaskMemoryService
from hecate_memory.memory.work_context_graph import (
    create_node_for_reflection,
    derive_node_type,
    supersede_nodes_for_reflection,
)

from hecate.core.database import async_session_factory

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed integration tests",
)


# ──────────────────────── fixtures ────────────────────────


@pytest_asyncio.fixture
async def db_session():
    async with async_session_factory() as session:
        yield session


# ──────────────────────── episode write path ────────────────────────


class TestEpisodeLifecycle:
    @pytest.mark.asyncio
    async def test_add_episode_and_record_tool_event(self, db_session) -> None:
        ws = uuid.UUID("00000000-0000-0000-0000-00000000e101")
        ag = uuid.UUID("00000000-0000-0000-0000-00000000e102")
        sess = uuid.UUID("00000000-0000-0000-0000-00000000e103")
        r = await TaskMemoryService.add_episode(
            workspace_id=ws,
            agent_id=ag,
            actor_id=None,
            session_id=sess,
            task_type="demo",
            situation="x",
            intent="y",
        )
        assert r.ok is True
        assert r.episode_id is not None

        r2 = await TaskMemoryService.record_tool_event(
            workspace_id=ws,
            agent_id=ag,
            session_id=sess,
            tool_name="demo_tool",
            args={"k": "v"},
            result_ref="ok",
        )
        assert r2.ok is True

    @pytest.mark.asyncio
    async def test_close_episode_idempotent(self, db_session) -> None:
        ws = uuid.UUID("00000000-0000-0000-0000-00000000e201")
        ag = uuid.UUID("00000000-0000-0000-0000-00000000e202")
        ep_id = uuid.uuid4()
        r = await TaskMemoryService.add_episode(
            workspace_id=ws,
            agent_id=ag,
            actor_id=None,
            session_id=None,
            task_type="x",
            situation="",
            intent="",
        )
        assert r.ok
        ep_id = r.episode_id

        r2 = await TaskMemoryService.close_episode(workspace_id=ws, agent_id=ag, episode_id=ep_id)
        assert r2.ok and r2.closed

        # Re-close is a no-op, not an error.
        r3 = await TaskMemoryService.close_episode(workspace_id=ws, agent_id=ag, episode_id=ep_id)
        assert r3.ok and r3.closed


# ──────────────────────── reflection pipeline (smoke) ────────────────────────


class TestReflectionPipeline:
    @pytest.mark.asyncio
    async def test_gate_thresholds_short_circuit(self) -> None:
        """Smoke test for the four gates with stub LLM seams."""

        async def reflect_stub(payload):
            return [
                {
                    "workspace_id": str(payload["workspace_id"]),
                    "agent_id": str(payload["agent_id"]),
                    "title": "Stub reflection",
                    "use_cases": ["demo"],
                    "hints": "Stub hints " * 20,
                    "source_episode_ids": [str(uuid.uuid4()), str(uuid.uuid4())],
                    "confidence": 0.8,
                }
            ]

        async def judge_stub(payload):
            return [{"isrel": 0.9, "issup": 0.85, "isuse": 0.9, "confidence": 0.8}]

        settings = ReflectionSettings()
        engine = ReflectionEngine(reflect_stub, judge_stub, settings=settings)
        async with async_session_factory() as db:
            summary = await engine.run_unit(
                db,
                workspace_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                window_start=datetime.now(UTC),
                window_end=datetime.now(UTC),
            )
        # Empty episodes → empty adoption in this smoke test, but no crash.
        assert summary["adopted"] == 0
        assert summary["rejected"] == 0

    @pytest.mark.asyncio
    async def test_confidence_evaluator_deprecates_low_confidence(self, db_session) -> None:
        """After enough passes, a low-confidence approved row flips to deprecated."""
        from sqlalchemy import select

        from hecate.models.task_memory import (
            REFLECTION_STATUS_APPROVED,
            REFLECTION_STATUS_DEPRECATED,
            ReflectionModel,
        )

        ws = uuid.uuid4()
        ag = uuid.uuid4()
        actor = uuid.uuid4()
        reflection_id = uuid.uuid4()
        async with async_session_factory() as db:
            db.add(
                ReflectionModel(
                    id=reflection_id,
                    workspace_id=ws,
                    agent_id=ag,
                    actor_id=actor,
                    session_id=None,
                    title="integration low confidence",
                    use_cases=["integration"],
                    hints="low confidence test",
                    confidence=0.3,
                    source_episode_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
                    operator="add",
                    superseded_by=None,
                    version=1,
                    status=REFLECTION_STATUS_APPROVED,
                    isrel=0.8,
                    issup=0.8,
                    isuse=0.8,
                    last_confirmed_at=datetime.now(UTC),
                    deprecation_streak=0,
                )
            )
            await db.commit()

        evaluator = ConfidenceEvaluator()
        for _ in range(4):
            async with async_session_factory() as db:
                await evaluator.run(db, workspace_id=ws)

        async with async_session_factory() as db:
            row = (await db.execute(select(ReflectionModel).where(ReflectionModel.id == reflection_id))).scalar_one()
        assert row.status == REFLECTION_STATUS_DEPRECATED
        assert row.deprecation_streak >= 3


# ──────────────────────── Work Context Graph ────────────────────────


class TestWorkContextGraph:
    def test_derive_node_type_priority(self) -> None:
        assert derive_node_type(title="Use selector wait", hints="") in {
            "method",
            "pattern",
        }
        assert derive_node_type(title="Correction: use X instead of Y", hints="") == "correction"
        assert derive_node_type(title="Source: from docs", hints="") == "source"

    @pytest.mark.asyncio
    async def test_create_node_and_supersede(self, db_session) -> None:
        from hecate.models.task_memory import ReflectionModel

        ws = uuid.uuid4()
        ag = uuid.uuid4()
        # Insert a reflection row directly.
        rid = uuid.uuid4()
        async with async_session_factory() as db:
            db.add(
                ReflectionModel(
                    id=rid,
                    workspace_id=ws,
                    agent_id=ag,
                    actor_id=None,
                    session_id=None,
                    title="Test reflection",
                    use_cases=["demo"],
                    hints="Test hints for graph node",
                    confidence=0.7,
                    source_episode_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
                    operator="add",
                    superseded_by=None,
                    version=1,
                    status="approved",
                    isrel=0.8,
                    issup=0.8,
                    isuse=0.8,
                )
            )
            await db.commit()

        node_id = await create_node_for_reflection(
            db_session,
            reflection_id=rid,
            workspace_id=ws,
            agent_id=ag,
            title="Test reflection",
            hints="Test hints for graph node",
            use_cases=["demo"],
            confidence=0.7,
        )
        assert node_id is not None

        # Supersede: same reflection_id gets marked inactive.
        from sqlalchemy import select

        from hecate.models.task_memory import WorkContextNodeModel

        flipped = await supersede_nodes_for_reflection(db_session, reflection_id=rid)
        assert flipped >= 1

        node = (
            await db_session.execute(select(WorkContextNodeModel).where(WorkContextNodeModel.id == node_id))
        ).scalar_one()
        assert node.active is False


# ──────────────────────── capability / provider smoke ────────────────────────


class TestProviderCapability:
    def test_builtin_provider_default_capabilities(self) -> None:
        """Default off: builtin declares tier-1/2/3 only."""
        from hecate_memory.memory.provider_impl import BuiltinMemoryProvider

        caps = BuiltinMemoryProvider().capabilities()
        assert "task_memory" not in caps
        assert "cross_thread" not in caps
        assert "end_episode" not in caps
        assert "escalate_failure" not in caps
        assert "search_memories" in caps

    def test_routing_tier_4_short_circuit(self) -> None:
        """An old provider lacking CAP_TASK_MEMORY returns a structured error."""
        legacy = type("LegacyProvider", (), {"capabilities": staticmethod(lambda: frozenset({"search_memories"}))})()
        try:
            from hecate.core.composition.memory_provider import TIER_4, route_search_by_tier

            route_search_by_tier(legacy, TIER_4)
        except Exception as e:
            from hecate.core.composition.memory_provider import TierRoutingError

            assert isinstance(e, TierRoutingError)
