"""Publication evidence must survive attempted migration downgrades."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.agent_version import AgentPublicationModel


@pytest.mark.parametrize("evidence", ["empty", "live", "deleted"])
async def test_downgrade_retains_all_publication_evidence(db_session: AsyncSession, monkeypatch, evidence) -> None:
    if evidence != "empty":
        agent = AgentModel(name="Published")
        db_session.add(agent)
        await db_session.flush()
        db_session.add(
            AgentPublicationModel(
                agent_id=agent.id,
                version=1,
                agent_config_hash="a" * 64,
                workspace_id=agent.workspace_id,
                outcome="gate_off",
                deleted=evidence == "deleted",
            )
        )
        await db_session.flush()

    path = Path(__file__).resolve().parents[2] / "alembic/versions/019_agent_publication_receipt.py"
    spec = importlib.util.spec_from_file_location("publication_receipt_revision", path)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    drops = []

    def run_downgrade(session):
        # Observe DDL calls without dropping the shared fixture schema.
        monkeypatch.setattr(
            revision,
            "op",
            SimpleNamespace(
                get_bind=session.connection,
                drop_index=lambda name, **kwargs: drops.append(name),
                drop_table=drops.append,
            ),
        )
        if evidence == "empty":
            revision.downgrade()
        else:
            with pytest.raises(RuntimeError, match="publication evidence must be retained"):
                revision.downgrade()

    await db_session.run_sync(run_downgrade)
    assert drops == (
        ["idx_agent_publications_workspace", "idx_agent_publications_agent", "agent_publications"]
        if evidence == "empty"
        else []
    )
