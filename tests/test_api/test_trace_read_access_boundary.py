"""Trace read access boundary (step10).

Covers the two REST entries of the trace read surface (``GET /api/traces``
and ``GET /api/traces/{trace_id}``) behind the evaluation access boundary:

- Every identity — system scope included — needs an active workspace
  (403 ``NO_WORKSPACE`` before any query); an explicit zero-UUID
  workspace stays a legal scope.
- Reads require VIEWER from the server-resolved auth context only. The
  list checks workspace and role before any query; the detail settles
  visibility with a minimal ownership/structure probe first, then the
  role floor, and only then loads full content — denials never load
  sensitive payloads.
- Root authorization comes from live Session/Agent row association: the
  session's workspace and its own linked agent must both be valid, a
  trace-level agent must resolve live in the workspace, and one
  legitimate link never masks a foreign, dead, or contradictory one.
  Orphans stay invisible; metadata and client filters grant nothing.
- Sparse OTel child spans (no session/agent attributes) inherit the
  root's authorization through a verified tree: ``parent_id`` stores the
  zero-padded UUID of the parent's ``metadata.otel.span_id``. Ambiguous
  roots, dangling references, cycles, disconnected rows sharing the
  trace_id, and spans with conflicting associations refuse the whole
  detail with a uniform 404 — no arbitrary first-row-as-root fallback.
- Content loads re-check liveness and refresh ORM state; pagination,
  filters, sorting, and legal empty lists keep their behavior with
  authorization applied first.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.agent import AgentModel
from hecate.models.organization import OrganizationModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.api.trace_access import TraceReadService
from tests.conftest import test_session_factory as shared_session_factory

ZERO_WS = uuid.UUID(int=0)
_BASE = "/api/traces"

# Marker content proving denials never leak payloads.
SECRET_ROOT = "SECRET-ROOT-PAYLOAD"
SECRET_SPAN = "SECRET-SPAN-PAYLOAD"


def _span_hex(n: int) -> str:
    """Deterministic 16-char OTel-style span id hex."""
    return f"{n:016x}"


def _span_uuid(hex16: str) -> uuid.UUID:
    """UUID form of an OTel span id, mirroring the span producer."""
    return uuid.UUID(hex16.rjust(32, "0"))


async def _workspace(db: AsyncSession, name: str, org: OrganizationModel | None = None) -> WorkspaceModel:
    name = f"{name}-{uuid.uuid4().hex[:8]}"
    user_id = uuid.uuid4()
    if org is None:
        org = OrganizationModel(name=f"Org {name}", slug=f"org-{name}", owner_id=user_id)
        db.add(org)
        await db.flush()
    ws = WorkspaceModel(org_id=org.id, name=name, slug=name)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=WorkspaceRole.VIEWER))
    await db.commit()
    return ws


def _member_as(ws: WorkspaceModel, role: WorkspaceRole | None) -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id,
        workspace_id=ws.id,
        role=role,
        auth_method="jwt",
        api_key_scope=None,
    )


def _system_in(ws: WorkspaceModel | None) -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=ws.org_id if ws else None,
        workspace_id=ws.id if ws else None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


def _no_workspace_context() -> AuthContext:
    return AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


@contextlib.asynccontextmanager
async def _client_as(ctx: AuthContext) -> AsyncIterator[AsyncClient]:
    from hecate.main import app

    async def override_auth() -> AuthContext:
        return ctx

    saved = app.dependency_overrides.get(get_auth_context)
    app.dependency_overrides[get_auth_context] = override_auth
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        if saved is None:
            app.dependency_overrides.pop(get_auth_context, None)
        else:
            app.dependency_overrides[get_auth_context] = saved


def _agent(db: AsyncSession, workspace_id: uuid.UUID, name: str) -> AgentModel:
    agent = AgentModel(
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        persona="p",
        model_config_db={"model": "gpt-4o"},
        tools=[],
        knowledge_base_ids=[],
        workspace_id=workspace_id,
    )
    db.add(agent)
    return agent


def _session(db: AsyncSession, agent_id: uuid.UUID, workspace_id: uuid.UUID, *, deleted: bool = False) -> SessionModel:
    row = SessionModel(agent_id=agent_id, workspace_id=workspace_id, deleted=deleted)
    db.add(row)
    return row


def _root_row(
    *,
    trace_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    span_hex: str | None = None,
    start_time: datetime | None = None,
    deleted: bool = False,
    name: str = "root",
) -> TraceModel:
    return TraceModel(
        trace_id=trace_id or uuid.uuid4(),
        parent_id=None,
        type="trace",
        name=name,
        session_id=session_id,
        agent_id=agent_id,
        input_data={"q": SECRET_ROOT} if name == "root" else {"q": f"{name}-input"},
        metadata_={"otel.span_id": span_hex or _span_hex(uuid.uuid4().int & 0xFFFFFFFFFFFF)},
        status="completed",
        start_time=start_time or datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(milliseconds=50),
        deleted=deleted,
    )


def _span_row(
    parent_hex: str,
    *,
    trace_id: uuid.UUID,
    span_hex: str,
    session_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    name: str = "span",
    start_time: datetime | None = None,
) -> TraceModel:
    return TraceModel(
        trace_id=trace_id,
        parent_id=_span_uuid(parent_hex),
        type="span",
        name=name,
        session_id=session_id,
        agent_id=agent_id,
        input_data={"q": SECRET_SPAN},
        metadata_={"otel.span_id": span_hex},
        status="completed",
        start_time=start_time or datetime.now(UTC) + timedelta(milliseconds=10),
        end_time=datetime.now(UTC) + timedelta(milliseconds=20),
    )


def _seed_graph(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    span_base: int,
    start_time: datetime | None = None,
) -> tuple[uuid.UUID, str]:
    """Root + two sparse child spans linked via OTel span-id markers."""
    trace_id = uuid.uuid4()
    root_hex = _span_hex(span_base)
    mid_hex = _span_hex(span_base + 1)
    leaf_hex = _span_hex(span_base + 2)
    db.add_all(
        [
            _root_row(
                trace_id=trace_id,
                session_id=session_id,
                agent_id=agent_id,
                span_hex=root_hex,
                start_time=start_time,
            ),
            _span_row(root_hex, trace_id=trace_id, span_hex=mid_hex),
            _span_row(mid_hex, trace_id=trace_id, span_hex=leaf_hex),
        ]
    )
    return trace_id, root_hex


async def _snapshot(db: AsyncSession) -> list[dict[str, Any]]:
    rows = (await db.execute(select(TraceModel).order_by(TraceModel.id))).mappings().all()
    return [dict(row) for row in rows]


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"


def _assert_trace_404(resp: Any) -> None:
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"]["message"] == "Trace not found"
    assert SECRET_ROOT not in resp.text and SECRET_SPAN not in resp.text


@pytest.fixture
async def env(db_session: AsyncSession):
    """Workspaces, agents, sessions, and a seeded trace population."""
    db = db_session
    ws = await _workspace(db, "trace-ws")
    org_a = ws.org_id
    same_org_ws = WorkspaceModel(
        org_id=org_a, name=f"same-org-{uuid.uuid4().hex[:8]}", slug=f"so-{uuid.uuid4().hex[:8]}"
    )
    db.add(same_org_ws)
    await db.flush()
    other_ws = await _workspace(db, "trace-other")  # different organization

    agent_ws = _agent(db, ws.id, "ws-agent")
    agent_foreign = _agent(db, other_ws.id, "foreign-agent")
    agent_same_org = _agent(db, same_org_ws.id, "same-org-agent")
    agent_dead = _agent(db, ws.id, "dead-agent")
    await db.flush()

    session_ws = _session(db, agent_ws.id, ws.id)
    session_foreign = _session(db, agent_foreign.id, other_ws.id)
    session_same_org = _session(db, agent_same_org.id, same_org_ws.id)
    session_dead = _session(db, agent_ws.id, ws.id, deleted=True)
    session_dead_agent = _session(db, agent_dead.id, ws.id)
    await db.flush()
    agent_dead.deleted = True  # session live, its agent soft-deleted
    await db.flush()

    # Authorized three-node graph.
    good_trace, _ = _seed_graph(db, session_id=session_ws.id, agent_id=agent_ws.id, span_base=1)

    # Same-organization foreign workspace graph.
    same_org_trace, _ = _seed_graph(db, session_id=session_same_org.id, agent_id=agent_same_org.id, span_base=10)
    # Cross-organization foreign graph.
    foreign_trace, _ = _seed_graph(db, session_id=session_foreign.id, agent_id=agent_foreign.id, span_base=20)
    # Dead session graph.
    dead_session_trace, _ = _seed_graph(db, session_id=session_dead.id, agent_id=agent_ws.id, span_base=30)
    # Live session whose linked agent is soft-deleted.
    dead_agent_trace, _ = _seed_graph(db, session_id=session_dead_agent.id, agent_id=None, span_base=40)
    # Orphan root (agent-only is seeded separately for the positive path).
    orphan_trace = uuid.uuid4()
    db.add(_root_row(trace_id=orphan_trace, span_hex=_span_hex(50)))
    # Root authorized via agent only (no session).
    agent_only_trace = uuid.uuid4()
    db.add(
        _root_row(
            trace_id=agent_only_trace,
            agent_id=agent_ws.id,
            span_hex=_span_hex(51),
        )
    )
    # Soft-deleted root in an otherwise identical authorized graph.
    deleted_trace = uuid.uuid4()
    db.add(
        _root_row(
            trace_id=deleted_trace,
            session_id=session_ws.id,
            agent_id=agent_ws.id,
            span_hex=_span_hex(52),
            deleted=True,
        )
    )
    await db.commit()

    from hecate.main import app

    async def shared_db():
        yield db_session

    saved_db = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = shared_db
    try:
        yield {
            "db": db,
            "ws": ws,
            "same_org_ws": same_org_ws,
            "other_ws": other_ws,
            "agent_ws": agent_ws,
            "agent_foreign": agent_foreign,
            "session_ws": session_ws,
            "session_foreign": session_foreign,
            "good_trace": good_trace,
            "same_org_trace": same_org_trace,
            "foreign_trace": foreign_trace,
            "dead_session_trace": dead_session_trace,
            "dead_agent_trace": dead_agent_trace,
            "orphan_trace": orphan_trace,
            "agent_only_trace": agent_only_trace,
            "deleted_trace": deleted_trace,
        }
    finally:
        if saved_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = saved_db


# ---------------------------------------------------------------------------
# Workspace requirement and role floors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_both_entries_require_workspace_before_any_query(
    env, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    """Workspace-less identities — system included — never reach a query."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async def unexpected(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("denied requests must not query")

    ctx = _system_in(None) if identity == "system" else _no_workspace_context()
    before = await _snapshot(env["db"])
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected)
        async with _client_as(ctx) as http:
            listing = await http.get(f"{_BASE}")
            detail = await http.get(f"{_BASE}/{uuid.uuid4()}")
    _assert_no_workspace(listing)
    _assert_no_workspace(detail)
    assert await _snapshot(env["db"]) == before


async def test_viewer_lists_and_reads(env) -> None:
    """VIEWER reads the authorized population and the full span tree."""
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        listing = await http.get(f"{_BASE}")
        assert listing.status_code == 200, listing.text
        listed = listing.json()
        listed_trace_ids = {item["trace_id"] for item in listed}
        assert str(env["good_trace"]) in listed_trace_ids
        assert str(env["agent_only_trace"]) in listed_trace_ids
        for invisible in (
            "same_org_trace",
            "foreign_trace",
            "dead_session_trace",
            "dead_agent_trace",
            "orphan_trace",
            "deleted_trace",
        ):
            assert str(env[invisible]) not in listed_trace_ids
        assert all(SECRET_ROOT not in str(item) for item in listed)  # list schema has no payloads

        detail = await http.get(f"{_BASE}/{env['good_trace']}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["trace"]["trace_id"] == str(env["good_trace"])
        assert len(body["spans"]) == 2  # sparse OTel children inherit the root's authorization


async def test_roleless_member_denied_after_visibility(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A role-less member resolves visibility first, then 403 — no content."""
    loaded: list[uuid.UUID] = []
    original = TraceReadService.load_trace_detail

    async def spy(self, authorized):  # noqa: ANN001
        loaded.append(authorized.root_id)
        return await original(self, authorized)

    monkeypatch.setattr(TraceReadService, "load_trace_detail", spy)
    roleless = _member_as(env["ws"], None)
    async with _client_as(roleless) as http:
        visible = await http.get(f"{_BASE}/{env['good_trace']}")
        invisible = await http.get(f"{_BASE}/{env['foreign_trace']}")
        listing = await http.get(f"{_BASE}")
    _assert_forbidden(visible)
    _assert_trace_404(invisible)
    _assert_forbidden(listing)
    assert loaded == []  # denied reads never load sensitive payloads


async def test_system_scope_with_workspace_reads(env) -> None:
    """System scope keeps the role exemption but stays workspace-bound."""
    system = _system_in(env["ws"])
    async with _client_as(system) as http:
        listing = await http.get(f"{_BASE}")
        assert listing.status_code == 200
        assert str(env["good_trace"]) in {item["trace_id"] for item in listing.json()}
        foreign = await http.get(f"{_BASE}/{env['foreign_trace']}")
        _assert_trace_404(foreign)


# ---------------------------------------------------------------------------
# Visibility: foreign (same org and cross org), deleted, orphan, conflicting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["viewer", "editor", "admin"])
async def test_invisible_traces_share_one_404(env, identity: str) -> None:
    """Foreign (same-org and cross-org), deleted, orphan, and dead-link
    traces look alike from every role."""
    ctx = {
        "viewer": _member_as(env["ws"], WorkspaceRole.VIEWER),
        "editor": _member_as(env["ws"], WorkspaceRole.EDITOR),
        "admin": _member_as(env["ws"], WorkspaceRole.ADMIN),
    }[identity]
    async with _client_as(ctx) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{uuid.uuid4()}"))
        _assert_trace_404(await http.get(f"{_BASE}/{env['same_org_trace']}"))
        _assert_trace_404(await http.get(f"{_BASE}/{env['foreign_trace']}"))
        _assert_trace_404(await http.get(f"{_BASE}/{env['dead_session_trace']}"))
        _assert_trace_404(await http.get(f"{_BASE}/{env['dead_agent_trace']}"))
        _assert_trace_404(await http.get(f"{_BASE}/{env['orphan_trace']}"))
        _assert_trace_404(await http.get(f"{_BASE}/{env['deleted_trace']}"))


async def test_contradictory_associations_never_authorize(env) -> None:
    """A legitimate link does not mask a foreign or dead one."""
    db = env["db"]
    # Session in ws + trace-level agent foreign.
    mixed_trace = uuid.uuid4()
    db.add(
        _root_row(
            trace_id=mixed_trace,
            session_id=env["session_ws"].id,
            agent_id=env["agent_foreign"].id,
            span_hex=_span_hex(60),
        )
    )
    # Session row in ws whose own agent binding is foreign.
    bad_session = _session(db, env["agent_foreign"].id, env["ws"].id)
    await db.flush()
    bad_binding_trace = uuid.uuid4()
    db.add(_root_row(trace_id=bad_binding_trace, session_id=bad_session.id, span_hex=_span_hex(61)))
    await db.commit()

    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{mixed_trace}"))
        _assert_trace_404(await http.get(f"{_BASE}/{bad_binding_trace}"))


async def test_child_with_conflicting_association_refuses_whole_detail(env) -> None:
    """A foreign-injected span under a shared trace_id mixes nothing in."""
    db = env["db"]
    trace_id = uuid.uuid4()
    root_hex = _span_hex(70)
    db.add(
        _root_row(
            trace_id=trace_id,
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
            span_hex=root_hex,
        )
    )
    db.add(
        _span_row(
            root_hex,
            trace_id=trace_id,
            span_hex=_span_hex(71),
            agent_id=env["agent_foreign"].id,  # explicit contradictory link
        )
    )
    await db.commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{trace_id}"))


async def test_child_with_matching_association_is_included(env) -> None:
    """Children repeating the root's own associations stay legitimate."""
    db = env["db"]
    trace_id = uuid.uuid4()
    root_hex = _span_hex(80)
    db.add(
        _root_row(
            trace_id=trace_id,
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
            span_hex=root_hex,
        )
    )
    db.add(
        _span_row(
            root_hex,
            trace_id=trace_id,
            span_hex=_span_hex(81),
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
        )
    )
    await db.commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        detail = await http.get(f"{_BASE}/{trace_id}")
        assert detail.status_code == 200, detail.text
        assert len(detail.json()["spans"]) == 1


# ---------------------------------------------------------------------------
# Structure safety: ambiguous roots, dangling, cycles, disconnected rows
# ---------------------------------------------------------------------------


async def test_ambiguous_root_refuses_detail(env) -> None:
    """Two parentless rows under one trace_id never pick an arbitrary root."""
    db = env["db"]
    trace_id = uuid.uuid4()
    db.add(
        _root_row(
            trace_id=trace_id,
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
            span_hex=_span_hex(90),
        )
    )
    db.add(
        _root_row(
            trace_id=trace_id,
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
            span_hex=_span_hex(91),
        )
    )
    await db.commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{trace_id}"))


async def test_dangling_parent_refuses_detail(env) -> None:
    """A child whose parent marker resolves to nothing refuses the detail."""
    db = env["db"]
    trace_id = uuid.uuid4()
    root_hex = _span_hex(100)
    db.add(
        _root_row(
            trace_id=trace_id,
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
            span_hex=root_hex,
        )
    )
    db.add(
        _span_row(
            _span_hex(999),  # no row carries this OTel span id
            trace_id=trace_id,
            span_hex=_span_hex(101),
        )
    )
    await db.commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{trace_id}"))


async def test_cycle_and_disconnected_rows_refuse_detail(env) -> None:
    """Cycles and foreign fragments never mix via a shared trace_id."""
    db = env["db"]
    # Cycle: two spans pointing at each other below an authorized root.
    cycle_trace = uuid.uuid4()
    root_hex = _span_hex(110)
    a_hex = _span_hex(111)
    b_hex = _span_hex(112)
    db.add(
        _root_row(
            trace_id=cycle_trace,
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
            span_hex=root_hex,
        )
    )
    db.add(_span_row(b_hex, trace_id=cycle_trace, span_hex=a_hex))
    db.add(_span_row(a_hex, trace_id=cycle_trace, span_hex=b_hex))
    # Disconnected authorized fragment under the same trace_id.
    solo_trace = cycle_trace
    db.add(
        _span_row(
            root_hex,
            trace_id=solo_trace,
            span_hex=_span_hex(113),
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
        )
    )
    await db.commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{cycle_trace}"))


# ---------------------------------------------------------------------------
# Zero-UUID workspace, filters, pagination, ordering
# ---------------------------------------------------------------------------


async def test_zero_uuid_workspace_is_a_legal_scope(env) -> None:
    """An explicit zero-UUID workspace scopes its own traces only."""
    db = env["db"]
    zero_agent = _agent(db, ZERO_WS, "zero-agent")
    await db.flush()
    zero_session = _session(db, zero_agent.id, ZERO_WS)
    await db.flush()
    zero_trace, _ = _seed_graph(db, session_id=zero_session.id, agent_id=zero_agent.id, span_base=120)
    await db.commit()

    ctx = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=ZERO_WS,
        role=WorkspaceRole.VIEWER,
        auth_method="jwt",
        api_key_scope=None,
    )
    async with _client_as(ctx) as http:
        listing = await http.get(f"{_BASE}")
        assert listing.status_code == 200
        listed = {item["trace_id"] for item in listing.json()}
        assert str(zero_trace) in listed
        assert str(env["good_trace"]) not in listed
        detail = await http.get(f"{_BASE}/{zero_trace}")
        assert detail.status_code == 200 and len(detail.json()["spans"]) == 2
        foreign = await http.get(f"{_BASE}/{env['good_trace']}")
        _assert_trace_404(foreign)


async def test_filters_narrow_but_never_grant(env) -> None:
    """session/agent/time filters only narrow the authorized population."""
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        by_session = await http.get(f"{_BASE}", params={"session_id": str(env["session_ws"].id)})
        assert by_session.status_code == 200
        assert {item["trace_id"] for item in by_session.json()} == {str(env["good_trace"])}

        # Foreign session filter does not surface foreign traces.
        foreign_filter = await http.get(f"{_BASE}", params={"session_id": str(env["session_foreign"].id)})
        assert foreign_filter.status_code == 200
        assert foreign_filter.json() == []

        no_match = await http.get(f"{_BASE}", params={"agent_id": str(uuid.uuid4())})
        assert no_match.status_code == 200 and no_match.json() == []

        window = datetime.now(UTC) - timedelta(hours=1)
        since = await http.get(f"{_BASE}", params={"start_time": window.isoformat()})
        assert since.status_code == 200
        assert str(env["good_trace"]) in {item["trace_id"] for item in since.json()}
        never = await http.get(f"{_BASE}", params={"end_time": (datetime.now(UTC) - timedelta(days=30)).isoformat()})
        assert never.status_code == 200 and never.json() == []


async def test_pagination_applies_after_authorization(env) -> None:
    """Pages walk only the authorized population, in start-time order."""
    db = env["db"]
    base = datetime.now(UTC) - timedelta(hours=2)
    seeded: set[str] = {str(env["good_trace"]), str(env["agent_only_trace"])}
    for i in range(4):
        trace_id, _ = _seed_graph(
            db,
            session_id=env["session_ws"].id,
            agent_id=env["agent_ws"].id,
            span_base=130 + i * 3,
            start_time=base + timedelta(minutes=i),
        )
        seeded.add(str(trace_id))
    # Foreign rows interleaved in time — never paged in.
    for i in range(3):
        _seed_graph(
            db,
            session_id=env["session_foreign"].id,
            agent_id=env["agent_foreign"].id,
            span_base=160 + i * 3,
            start_time=base + timedelta(minutes=i),
        )
    await db.commit()

    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    collected: list[str] = []
    async with _client_as(viewer) as http:
        page = await http.get(f"{_BASE}", params={"limit": 3, "offset": 0})
        assert page.status_code == 200
        assert len(page.json()) == 3
        collected.extend(item["trace_id"] for item in page.json())
        second = await http.get(f"{_BASE}", params={"limit": 3, "offset": 3})
        assert second.status_code == 200
        collected.extend(item["trace_id"] for item in second.json())
        empty = await http.get(f"{_BASE}", params={"limit": 3, "offset": 99})
        assert empty.status_code == 200 and empty.json() == []
        full = await http.get(f"{_BASE}", params={"limit": 100})
        all_ids = [item["trace_id"] for item in full.json()]
        times = await http.get(f"{_BASE}", params={"limit": 100})
    assert set(collected) <= seeded
    assert set(all_ids) == seeded
    start_times = [datetime.fromisoformat(item["start_time"]) for item in times.json()]
    assert start_times == sorted(start_times, reverse=True)


# ---------------------------------------------------------------------------
# Post-gate changes and cached-object refresh
# ---------------------------------------------------------------------------


async def test_detail_rechecks_liveness_between_probe_and_content(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows deleted after the visibility probe never load content."""
    trace_id = env["good_trace"]

    async def delete_rows(session: AsyncSession) -> None:
        rows = (
            (
                await session.execute(
                    select(TraceModel).where(TraceModel.trace_id == trace_id, TraceModel.parent_id.is_(None))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.deleted = True

    original = TraceReadService.load_trace_detail

    async def interposed(self, authorized):  # noqa: ANN001
        async with shared_session_factory() as other:
            await delete_rows(other)
            await other.commit()
        return await original(self, authorized)

    monkeypatch.setattr(TraceReadService, "load_trace_detail", interposed)
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        response = await http.get(f"{_BASE}/{trace_id}")
    _assert_trace_404(response)


async def test_list_refreshes_stale_cached_objects(env) -> None:
    """Identity-map entries from earlier reads never resurface foreign rows."""
    db = env["db"]
    # Warm the shared session's identity map with the foreign graph.
    await db.execute(select(TraceModel).where(TraceModel.trace_id == env["foreign_trace"]))
    # Move the good trace's session into the foreign workspace behind the cache.
    async with shared_session_factory() as other:
        session_row = await other.get(SessionModel, env["session_ws"].id)
        session_row.workspace_id = env["other_ws"].id
        await other.commit()

    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        listing = await http.get(f"{_BASE}")
        assert listing.status_code == 200
        listed = {item["trace_id"] for item in listing.json()}
        assert str(env["good_trace"]) not in listed  # authorization re-evaluated in SQL
        assert str(env["foreign_trace"]) not in listed
        detail = await http.get(f"{_BASE}/{env['good_trace']}")
        _assert_trace_404(detail)


async def test_denied_detail_never_loads_payloads(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Foreign and structurally-unsafe details load no content at all."""
    loaded: list[uuid.UUID] = []
    original = TraceReadService.load_trace_detail

    async def spy(self, authorized):  # noqa: ANN001
        loaded.append(authorized.root_id)
        return await original(self, authorized)

    monkeypatch.setattr(TraceReadService, "load_trace_detail", spy)
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        for target in ("foreign_trace", "orphan_trace", "dead_session_trace", "deleted_trace"):
            await http.get(f"{_BASE}/{env[target]}")
        random = await http.get(f"{_BASE}/{uuid.uuid4()}")
    _assert_trace_404(random)
    assert loaded == []


@pytest.mark.parametrize(
    "change",
    [
        "session-move",
        "linked-agent-deleted",
        "root-agent",
        "child-agent",
        "trace-id",
        "parent",
        "span-id",
        "extra-root",
    ],
)
async def test_content_query_rechecks_scope_and_shape(env, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    """A change immediately before the content SQL never loads any payload."""
    db = env["db"]
    trace_id = env["good_trace"]
    children = (
        (await db.execute(select(TraceModel).where(TraceModel.trace_id == trace_id, TraceModel.parent_id.is_not(None))))
        .scalars()
        .all()
    )
    child_id = next(row.id for row in children if row.metadata_["otel.span_id"] == _span_hex(2))
    original = AsyncSession.execute
    content_ids: list[uuid.UUID] = []
    changed = False

    async def execute(self, statement, *args, **kwargs):  # noqa: ANN001
        nonlocal changed
        content = self is db and statement.is_select and "traces.input_data" in str(statement)
        if content and not changed:
            changed = True
            async with shared_session_factory() as other:
                if change == "session-move":
                    stmt = (
                        update(SessionModel)
                        .where(SessionModel.id == env["session_ws"].id)
                        .values(workspace_id=env["other_ws"].id)
                    )
                elif change == "linked-agent-deleted":
                    stmt = update(AgentModel).where(AgentModel.id == env["agent_ws"].id).values(deleted=True)
                elif change in ("root-agent", "child-agent"):
                    target = TraceModel.parent_id.is_(None) if change == "root-agent" else TraceModel.id == child_id
                    stmt = (
                        update(TraceModel)
                        .where(TraceModel.trace_id == trace_id, target)
                        .values(agent_id=env["agent_foreign"].id)
                    )
                elif change == "trace-id":
                    stmt = update(TraceModel).where(TraceModel.id == child_id).values(trace_id=uuid.uuid4())
                elif change == "parent":
                    stmt = update(TraceModel).where(TraceModel.id == child_id).values(parent_id=uuid.uuid4())
                elif change == "span-id":
                    stmt = (
                        update(TraceModel)
                        .where(TraceModel.id == child_id)
                        .values(metadata_={"otel.span_id": _span_hex(999)})
                    )
                else:
                    other.add(_root_row(trace_id=trace_id, agent_id=env["agent_ws"].id))
                    stmt = None
                if stmt is not None:
                    await original(other, stmt)
                await other.commit()
        result = await original(self, statement, *args, **kwargs)
        if content:
            frozen = result.freeze()
            content_ids.extend(row.id for row in frozen().scalars().all())
            return frozen()
        return result

    monkeypatch.setattr(AsyncSession, "execute", execute)
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(f"{_BASE}/{trace_id}")
    assert changed
    assert content_ids == []
    _assert_trace_404(response)


async def test_duplicate_span_alias_refuses_detail(env) -> None:
    """Repeated OTel ids must not silently choose one parent from a dict."""
    db = env["db"]
    trace_id = uuid.uuid4()
    root = _root_row(trace_id=trace_id, agent_id=env["agent_ws"].id, span_hex=_span_hex(200))
    db.add_all(
        [
            root,
            _span_row(_span_hex(200), trace_id=trace_id, span_hex=_span_hex(201)),
            _span_row(_span_hex(200), trace_id=trace_id, span_hex=_span_hex(201)),
            _span_row(_span_hex(201), trace_id=trace_id, span_hex=_span_hex(202)),
        ]
    )
    await db.commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{trace_id}"))


async def test_span_alias_and_primary_key_collision_refuses_detail(env) -> None:
    """A parent reference resolving to two distinct rows is ambiguous."""
    db = env["db"]
    trace_id = uuid.uuid4()
    root = _root_row(trace_id=trace_id, agent_id=env["agent_ws"].id, span_hex=_span_hex(210))
    db.add(root)
    await db.flush()
    child = _span_row(_span_hex(210), trace_id=trace_id, span_hex=_span_hex(211))
    child.id = _span_uuid(_span_hex(210))
    child.parent_id = root.id
    db.add_all([child, _span_row(_span_hex(210), trace_id=trace_id, span_hex=_span_hex(212))])
    await db.commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{trace_id}"))


@pytest.mark.parametrize("marker", [[], {}, 1, True, "bad", "0000000000000000"])
async def test_malformed_span_alias_refuses_detail(env, marker: Any) -> None:
    """A present but invalid OTel id cannot establish a verified tree."""
    root = _root_row(agent_id=env["agent_ws"].id)
    root.metadata_ = {"otel.span_id": marker}
    env["db"].add(root)
    await env["db"].commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        _assert_trace_404(await http.get(f"{_BASE}/{root.trace_id}"))


@pytest.mark.parametrize("zero_primary_key", [False, True])
async def test_direct_primary_key_links_without_otel_markers_remain_readable(env, zero_primary_key: bool) -> None:
    """Non-OTel producers may use unambiguous row primary keys as parents."""
    db = env["db"]
    root = _root_row(agent_id=env["agent_ws"].id)
    root.metadata_ = {}
    if zero_primary_key:
        root.id = uuid.UUID(int=0)
    db.add(root)
    await db.flush()
    child = _span_row(_span_hex(220), trace_id=root.trace_id, span_hex=_span_hex(221))
    child.metadata_ = {}
    child.parent_id = root.id
    db.add(child)
    await db.commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(f"{_BASE}/{root.trace_id}")
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["spans"]] == [str(child.id)]


async def test_real_otel_bridge_tree_is_readable(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real SDK spans survive bridge persistence and sparse-child authorization."""
    from hecate_ops.span_processor import HecateTraceSpanProcessor
    from opentelemetry.sdk.trace import TracerProvider

    processor = HecateTraceSpanProcessor(db_session_factory=shared_session_factory)
    monkeypatch.setattr(processor, "_ensure_consumer", lambda: None)
    provider = TracerProvider()
    provider.add_span_processor(processor)
    tracer = provider.get_tracer("trace-boundary-test")
    try:
        with tracer.start_as_current_span("session:test", attributes={"session.id": str(env["session_ws"].id)}) as root:
            trace_id = uuid.UUID(int=root.get_span_context().trace_id)
            with tracer.start_as_current_span("tool:test", attributes={"review.marker": "bridge-result"}) as child:
                pass
        batch = []
        while not processor._queue.empty():
            batch.append(processor._queue.get_nowait())
        await processor._flush_batch(batch)
        async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
            response = await http.get(f"{_BASE}/{trace_id}")
        assert response.status_code == 200, response.text
        spans = response.json()["spans"]
        assert len(spans) == 1
        assert spans[0]["session_id"] is None and spans[0]["agent_id"] is None
        assert spans[0]["metadata_"]["review.marker"] == "bridge-result"
        assert spans[0]["metadata_"]["otel.span_id"] == f"{child.get_span_context().span_id:016x}"
    finally:
        provider.shutdown()


@pytest.mark.parametrize("entry", ["list", "detail"])
async def test_cached_foreign_content_is_refreshed_when_row_becomes_owned(env, entry: str) -> None:
    """A strongly held ORM row must not override current authorized content."""
    db = env["db"]
    cached = _root_row(agent_id=env["agent_foreign"].id, name="old-private-name")
    cached.input_data = {"private": "old-private-content"}
    db.add(cached)
    await db.commit()
    async with shared_session_factory() as other:
        await other.execute(
            update(TraceModel)
            .where(TraceModel.id == cached.id)
            .values(agent_id=env["agent_ws"].id, name="current-owned-name", input_data={"public": "current-content"})
        )
        await other.commit()
    assert cached.name == "old-private-name"
    path = _BASE if entry == "list" else f"{_BASE}/{cached.trace_id}"
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(path)
    assert response.status_code == 200, response.text
    assert "current-owned-name" in response.text
    assert "old-private" not in response.text


async def test_file_sqlite_rechecks_association_at_content_read(governance_session_factory) -> None:
    """Independent connections reproduce revocation after the minimal probe."""
    factory = governance_session_factory
    async with factory.kw["bind"].begin() as connection:
        await connection.run_sync(
            lambda sync: TraceModel.metadata.create_all(
                sync, tables=[AgentModel.__table__, SessionModel.__table__, TraceModel.__table__]
            )
        )
    workspace_id = uuid.uuid4()
    async with factory() as setup:
        agent = _agent(setup, workspace_id, "file-agent")
        await setup.flush()
        session = _session(setup, agent.id, workspace_id)
        await setup.flush()
        trace_id, _ = _seed_graph(setup, session_id=session.id, agent_id=agent.id, span_base=230)
        session_id = session.id
        await setup.commit()
    async with factory() as reader:
        service = TraceReadService(reader)
        authorized = await service.authorize_trace(trace_id, workspace_id)
        async with factory() as writer:
            await writer.execute(
                update(SessionModel).where(SessionModel.id == session_id).values(workspace_id=uuid.uuid4())
            )
            await writer.commit()
        with pytest.raises(LookupError, match="^Trace not found$"):
            await service.load_trace_detail(authorized)
