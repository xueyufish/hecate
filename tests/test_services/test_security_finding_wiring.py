"""Tests for the L0 output-side findings wiring substrate."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from hecate.ops.security.findings_writer import (
    FindingWriterAdapter,
    SecurityFindingWriter,
)


class TestSecurityFindingWriter:
    def test_is_active_requires_db_org_workspace(self) -> None:
        w = SecurityFindingWriter(db=None, org_id=None, workspace_id=None)
        assert w.is_active is False

    def test_is_active_when_full_context(self) -> None:
        w = SecurityFindingWriter(
            db=SimpleNamespace(),
            org_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
        )
        assert w.is_active is True


class TestFindingWriterAdapter:
    def test_callable_dispatch(self) -> None:
        called = []

        def writer(**kwargs):
            called.append(kwargs)
            return "ok"

        adapter = FindingWriterAdapter(writer)
        assert adapter.is_active is True
        # .write is async in adapter
        import asyncio

        result = asyncio.run(
            adapter.write(entity_type="X", value="v", start=0, end=1, score=1.0, recognizer="r", action="audit")
        )
        assert result == "ok"
        assert called[0]["entity_type"] == "X"
