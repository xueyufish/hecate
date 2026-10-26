"""Prompt management access boundary (step10).

Covers the thirteen REST entries of the prompt management surface (CRUD,
version list/detail, rollback, by-label lookup, diff, analytics, compare,
AI summary) behind the evaluation access boundary:

- Every identity — system scope included — needs an active workspace
  (403 ``NO_WORKSPACE`` before any query); an explicit zero-UUID
  workspace stays a legal scope.
- Role floors: VIEWER to read, EDITOR to create/update/delete/roll back
  and to request AI summaries (they invoke a model) — enforced from the
  server-resolved auth context only, after resource visibility and
  before any business query, template validation, or model call.
- Visibility: missing, foreign, soft-deleted, and workspace-inconsistent
  prompts/versions share one 404 that leaks neither the reason nor any
  content; legacy versions whose workspace disagrees with the parent
  prompt are never listed, copied, rolled back, or analyzed.
- Protected labels stay admin-only on every path that could put them on
  a new version: explicit create/update, label carry-over by omission,
  and rollback copies.
- Writes re-verify the parent binding inside the transaction with fresh
  reads; a prompt moved or deleted after the API precheck is refused.
- Trace analytics authorize through Session/Agent row association into
  the caller's workspace; prompt metadata is a metric selector only.
  Orphaned, foreign, soft-deleted, and contradictory associations never
  feed statistics, and authorized paths return real numbers.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.agent import AgentModel
from hecate.models.organization import OrganizationModel
from hecate.models.prompt import PromptModel, PromptVersionModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.studio.prompts.service import PromptService
from tests.conftest import test_session_factory as shared_session_factory

ZERO_WS = uuid.UUID(int=0)
_BASE = "/api/prompts"


async def _workspace_with_role(db: AsyncSession, name: str, role: WorkspaceRole) -> WorkspaceModel:
    name = f"{name}-{uuid.uuid4().hex[:8]}"
    user_id = uuid.uuid4()
    org = OrganizationModel(name=f"Org {name}", slug=f"org-{name}", owner_id=user_id)
    db.add(org)
    await db.flush()
    ws = WorkspaceModel(org_id=org.id, name=name, slug=name)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMemberModel(user_id=user_id, workspace_id=ws.id, role=role))
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


def _seed_prompt(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    template: str,
    *,
    labels: list[str] | None = None,
    versions: int = 1,
    deleted: bool = False,
    current_version: int | None = None,
) -> PromptModel:
    prompt = PromptModel(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=f"p-{uuid.uuid4().hex[:10]}",
        current_version=current_version or versions,
        deleted=deleted,
    )
    db.add(prompt)
    version_labels = labels if labels is not None else []
    for v in range(1, versions + 1):
        db.add(
            PromptVersionModel(
                prompt_id=prompt.id,
                version=v,
                template=template if v == 1 else f"{template} v{v}",
                variables=[],
                labels=version_labels if v == versions else [],
                workspace_id=workspace_id,
            )
        )
    return prompt


def _seed_trace(
    *,
    prompt_id: uuid.UUID,
    version: int | str,
    session_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    status: str = "completed",
    tokens: int = 10,
    latency_ms: int = 100,
    deleted: bool = False,
) -> TraceModel:
    now = datetime.now(UTC)
    return TraceModel(
        trace_id=uuid.uuid4(),
        type="llm",
        name="call",
        session_id=session_id,
        agent_id=agent_id,
        metadata_={"prompt_id": str(prompt_id), "prompt_version": version},
        usage={"total_tokens": tokens},
        status=status,
        start_time=now,
        end_time=now + timedelta(milliseconds=latency_ms),
        deleted=deleted,
    )


async def _snapshot(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for model in (PromptModel, PromptVersionModel, TraceModel):
        table = model.__table__
        rows = (await db.execute(select(table).order_by(table.c.id))).mappings().all()
        snapshot[table.name] = [dict(row) for row in rows]
    return snapshot


def _assert_no_workspace(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json() == {
        "detail": {"error": {"code": "NO_WORKSPACE", "message": "An active workspace is required", "details": None}}
    }


def _assert_forbidden(resp: Any) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"


def _assert_not_found(resp: Any, message: str) -> None:
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"]["error"]["message"] == message
    assert body["detail"]["error"]["code"] == "NOT_FOUND"


def _assert_prompt_404(resp: Any) -> None:
    _assert_not_found(resp, "Prompt not found")


def _assert_version_404(resp: Any) -> None:
    _assert_not_found(resp, "Prompt or version not found")


@pytest.fixture
def llm_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub LLMService.chat, recording every call; content is controllable."""
    calls: list[dict[str, Any]] = []

    class _Resp:
        def __init__(self, content: str) -> None:
            self.content = content

    async def stub_chat(self, messages, model=None, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"messages": messages, "model": model})
        return _Resp(f"stubbed summary ({len(calls)})")

    monkeypatch.setattr("hecate_llm.service.LLMService.chat", stub_chat)
    return {"calls": calls}


@pytest.fixture
async def env(db_session: AsyncSession):
    """App boundary with resolved auth contexts, seeded graph, and spies."""
    ws = await _workspace_with_role(db_session, "prompt-ws", WorkspaceRole.VIEWER)
    other_ws = await _workspace_with_role(db_session, "prompt-other", WorkspaceRole.VIEWER)

    # Two-version prompt with analytics-visible traces on version 2.
    prompt = _seed_prompt(db_session, ws.id, "Answer {{q}}.", versions=2)
    agent = AgentModel(
        name=f"pa-agent-{uuid.uuid4().hex[:8]}",
        persona="p",
        model_config_db={"model": "gpt-4o"},
        tools=[],
        knowledge_base_ids=[],
        workspace_id=ws.id,
    )
    other_agent = AgentModel(
        name=f"pa-agent-{uuid.uuid4().hex[:8]}",
        persona="p",
        model_config_db={"model": "gpt-4o"},
        tools=[],
        knowledge_base_ids=[],
        workspace_id=other_ws.id,
    )
    db_session.add_all([agent, other_agent])
    await db_session.flush()
    session_row = SessionModel(agent_id=agent.id, workspace_id=ws.id)
    other_session = SessionModel(agent_id=other_agent.id, workspace_id=other_ws.id)
    deleted_session = SessionModel(agent_id=agent.id, workspace_id=ws.id, deleted=True)
    db_session.add_all([session_row, other_session, deleted_session])
    await db_session.flush()

    db_session.add_all(
        [
            # Authorized: int version marker via session association.
            _seed_trace(
                prompt_id=prompt.id,
                version=2,
                session_id=session_row.id,
                agent_id=agent.id,
                tokens=10,
                latency_ms=100,
            ),
            # Authorized: string version marker via agent-only association; error row.
            _seed_trace(
                prompt_id=prompt.id,
                version="2",
                session_id=None,
                agent_id=agent.id,
                status="error",
                tokens=5,
                latency_ms=300,
            ),
        ]
    )
    await db_session.commit()

    # Excluded trace shapes for analytics (authorized one + each bad shape).
    excluded = [
        _seed_trace(prompt_id=prompt.id, version=2, session_id=other_session.id, agent_id=other_agent.id),
        _seed_trace(prompt_id=prompt.id, version=2, session_id=None, agent_id=None),
        _seed_trace(prompt_id=prompt.id, version=2, session_id=session_row.id, agent_id=other_agent.id),
        _seed_trace(prompt_id=prompt.id, version=2, session_id=deleted_session.id, agent_id=agent.id),
        _seed_trace(prompt_id=prompt.id, version=2, session_id=session_row.id, agent_id=agent.id, deleted=True),
        _seed_trace(prompt_id=prompt.id, version=7, session_id=session_row.id, agent_id=agent.id),
    ]
    db_session.add_all(excluded)
    await db_session.commit()

    # Cross-workspace, soft-deleted, and legacy-mismatched prompts.
    foreign_prompt = _seed_prompt(db_session, other_ws.id, "Foreign.", versions=2)
    deleted_prompt = _seed_prompt(db_session, ws.id, "Deleted.", versions=1, deleted=True)
    labeled_prompt = _seed_prompt(db_session, ws.id, "Labeled.", labels=["production"], versions=1)
    zero_prompt = _seed_prompt(db_session, ZERO_WS, "Zero.", versions=1)
    stale_prompt = _seed_prompt(db_session, ws.id, "Stale.", versions=1)
    stale_version = PromptVersionModel(
        prompt_id=stale_prompt.id,
        version=2,
        template="Legacy mismatched row.",
        variables=[],
        labels=["deploy"],
        workspace_id=other_ws.id,  # legacy: disagrees with the parent prompt
    )
    db_session.add(stale_version)
    await db_session.commit()

    from hecate.main import app

    async def shared_db():
        yield db_session

    saved_db = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = shared_db
    try:
        yield {
            "db": db_session,
            "ws": ws,
            "other_ws": other_ws,
            "prompt": prompt,
            "agent": agent,
            "session_row": session_row,
            "foreign_prompt": foreign_prompt,
            "deleted_prompt": deleted_prompt,
            "labeled_prompt": labeled_prompt,
            "zero_prompt": zero_prompt,
            "stale_prompt": stale_prompt,
            "stale_version": stale_version,
        }
    finally:
        if saved_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = saved_db


def _interpose(
    monkeypatch: pytest.MonkeyPatch,
    cls: type,
    method_name: str,
    mutate,
) -> None:
    """Run ``mutate(session)`` on a fresh session just before the real call."""
    original = getattr(cls, method_name)

    async def wrapper(self, *args, **kwargs):  # noqa: ANN001, ANN202
        async with shared_session_factory() as session:
            await mutate(session)
            await session.commit()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(cls, method_name, wrapper)


# ---------------------------------------------------------------------------
# Workspace requirement: every identity, every endpoint, before any query
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["member", "system"])
async def test_all_entries_require_workspace_before_any_query(
    env, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    """Workspace-less identities — system included — never reach a query."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async def unexpected(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("denied requests must not query")

    ctx = _system_in(None) if identity == "system" else _no_workspace_context()
    missing = uuid.uuid4()
    before = await _snapshot(env["db"])
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", unexpected)
        async with _client_as(ctx) as http:
            responses = [
                await http.post(f"{_BASE}", json={"name": "n", "template": "T"}),
                await http.get(f"{_BASE}"),
                await http.get(f"{_BASE}/{missing}"),
                await http.put(f"{_BASE}/{missing}", json={"template": "T"}),
                await http.delete(f"{_BASE}/{missing}"),
                await http.get(f"{_BASE}/{missing}/versions"),
                await http.get(f"{_BASE}/{missing}/versions/1"),
                await http.post(f"{_BASE}/{missing}/rollback/1"),
                await http.get(f"{_BASE}/by-label/production"),
                await http.get(f"{_BASE}/{missing}/diff", params={"from_version": 1, "to_version": 2}),
                await http.get(f"{_BASE}/{missing}/analytics", params={"version": 1}),
                await http.get(f"{_BASE}/{missing}/compare", params={"from_version": 1, "to_version": 2}),
                await http.post(f"{_BASE}/{missing}/versions/1/summary"),
            ]
    for resp in responses:
        _assert_no_workspace(resp)
    assert await _snapshot(env["db"]) == before


# ---------------------------------------------------------------------------
# Role floors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [WorkspaceRole.VIEWER, None])
async def test_create_below_editor_denied_before_business(env, role) -> None:
    """Viewers and role-less members are denied before validation."""
    before = await _snapshot(env["db"])
    async with _client_as(_member_as(env["ws"], role)) as http:
        denied = await http.post(f"{_BASE}", json={"name": "n", "template": "T {{ x"})
        business_invalid = await http.post(f"{_BASE}", json={"name": "n", "template": "T {{ x"})
    _assert_forbidden(denied)
    _assert_forbidden(business_invalid)
    assert await _snapshot(env["db"]) == before


async def test_viewer_reads_but_never_writes(env, llm_spy) -> None:
    """VIEWER reads everything read-side; every write, summary included, 403s."""
    ws = env["ws"]
    viewer = _member_as(ws, WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        listing = await http.get(f"{_BASE}")
        assert listing.status_code == 200
        detail = await http.get(f"{_BASE}/{env['prompt'].id}")
        assert detail.status_code == 200 and detail.json()["id"] == str(env["prompt"].id)
        versions = await http.get(f"{_BASE}/{env['prompt'].id}/versions")
        assert versions.status_code == 200 and len(versions.json()) == 2
        version = await http.get(f"{_BASE}/{env['prompt'].id}/versions/1")
        assert version.status_code == 200 and version.json()["template"] == "Answer {{q}}."
        labeled = await http.get(f"{_BASE}/by-label/production")
        assert labeled.status_code == 200 and labeled.json()["id"] == str(env["labeled_prompt"].id)
        diff = await http.get(f"{_BASE}/{env['prompt'].id}/diff", params={"from_version": 1, "to_version": 2})
        assert diff.status_code == 200 and diff.json()["added_lines"] >= 1
        analytics = await http.get(f"{_BASE}/{env['prompt'].id}/analytics", params={"version": 2})
        assert analytics.status_code == 200 and analytics.json()["total_calls"] == 2
        compare = await http.get(f"{_BASE}/{env['prompt'].id}/compare", params={"from_version": 1, "to_version": 2})
        assert compare.status_code == 200 and "deltas" in compare.json()

        before = await _snapshot(env["db"])
        writes = [
            await http.post(f"{_BASE}", json={"name": "n", "template": "T"}),
            await http.put(f"{_BASE}/{env['prompt'].id}", json={"template": "changed"}),
            await http.delete(f"{_BASE}/{env['prompt'].id}"),
            await http.post(f"{_BASE}/{env['prompt'].id}/rollback/1"),
            await http.post(f"{_BASE}/{env['prompt'].id}/versions/2/summary"),
        ]
    for resp in writes:
        _assert_forbidden(resp)
    assert llm_spy["calls"] == []
    assert await _snapshot(env["db"]) == before


async def test_editor_writes_succeed(env, llm_spy) -> None:
    """EDITOR updates, deletes, rolls back, and requests summaries."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    created = _seed_prompt(env["db"], env["ws"].id, "Editable.", versions=2)
    await env["db"].commit()
    async with _client_as(editor) as http:
        update = await http.put(f"{_BASE}/{created.id}", json={"template": "Edited body."})
        assert update.status_code == 200 and update.json()["current_version"] == 3
        rollback = await http.post(f"{_BASE}/{created.id}/rollback/1")
        assert rollback.status_code == 200 and rollback.json()["current_version"] == 4
        assert rollback.json()["version"]["template"] == "Editable."
        summary = await http.post(f"{_BASE}/{created.id}/versions/3/summary")
        assert summary.status_code == 200, summary.text
        assert len(llm_spy["calls"]) == 1
        assert summary.json()["summary"].startswith("stubbed summary")
        delete = await http.delete(f"{_BASE}/{created.id}")
        assert delete.status_code == 204
        gone = await http.get(f"{_BASE}/{created.id}")
        assert gone.status_code == 404
        fresh = await http.post(f"{_BASE}", json={"name": f"n-{uuid.uuid4().hex[:6]}", "template": "T"})
        assert fresh.status_code == 201


async def test_roleless_member_denied_after_visibility(env) -> None:
    """A role-less member still resolves visibility first, then 403."""
    roleless = _member_as(env["ws"], None)
    async with _client_as(roleless) as http:
        visible = await http.get(f"{_BASE}/{env['prompt'].id}")
        invisible = await http.get(f"{_BASE}/{uuid.uuid4()}")
    _assert_forbidden(visible)
    _assert_prompt_404(invisible)


async def test_system_scope_with_explicit_workspace_keeps_floors(env, llm_spy) -> None:
    """System scope with a workspace reads/writes via exemption — but never admin."""
    system = _system_in(env["ws"])
    async with _client_as(system) as http:
        read = await http.get(f"{_BASE}/{env['prompt'].id}")
        assert read.status_code == 200
        update = await http.put(f"{_BASE}/{env['prompt'].id}", json={"template": "System edit."})
        assert update.status_code == 200 and update.json()["current_version"] == 3
        created = await http.post(
            f"{_BASE}", json={"name": f"sys-{uuid.uuid4().hex[:6]}", "template": "T", "labels": ["production"]}
        )
        _assert_forbidden(created)
        plain = await http.post(f"{_BASE}", json={"name": f"sys-{uuid.uuid4().hex[:6]}", "template": "T"})
        assert plain.status_code == 201
    assert llm_spy["calls"] == []


# ---------------------------------------------------------------------------
# Visibility: missing / foreign / soft-deleted / workspace-inconsistent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("identity", ["viewer", "editor", "admin"])
async def test_invisible_prompts_share_one_404(env, identity: str) -> None:
    """Missing, foreign, and deleted prompts look alike on every entry."""
    ctx = {
        "viewer": _member_as(env["ws"], WorkspaceRole.VIEWER),
        "editor": _member_as(env["ws"], WorkspaceRole.EDITOR),
        "admin": _member_as(env["ws"], WorkspaceRole.ADMIN),
    }[identity]
    targets = [uuid.uuid4(), env["foreign_prompt"].id, env["deleted_prompt"].id]
    before = await _snapshot(env["db"])
    async with _client_as(ctx) as http:
        for target in targets:
            _assert_prompt_404(await http.get(f"{_BASE}/{target}"))
            _assert_prompt_404(await http.put(f"{_BASE}/{target}", json={"template": "x"}))
            _assert_prompt_404(await http.delete(f"{_BASE}/{target}"))
            _assert_prompt_404(await http.get(f"{_BASE}/{target}/versions"))
            _assert_version_404(await http.get(f"{_BASE}/{target}/versions/1"))
            _assert_version_404(await http.post(f"{_BASE}/{target}/rollback/1"))
            _assert_version_404(await http.get(f"{_BASE}/{target}/diff", params={"from_version": 1, "to_version": 2}))
            _assert_version_404(await http.get(f"{_BASE}/{target}/analytics", params={"version": 1}))
            _assert_version_404(
                await http.get(f"{_BASE}/{target}/compare", params={"from_version": 1, "to_version": 2})
            )
            _assert_version_404(await http.post(f"{_BASE}/{target}/versions/1/summary"))
    assert await _snapshot(env["db"]) == before


@pytest.mark.parametrize("identity", ["viewer", "editor"])
async def test_invisible_versions_share_one_404(env, identity: str) -> None:
    """Missing, deleted, and workspace-inconsistent versions look alike."""
    ctx = _member_as(env["ws"], WorkspaceRole.VIEWER if identity == "viewer" else WorkspaceRole.EDITOR)
    stale_prompt = env["stale_prompt"]
    stale_prompt_id = stale_prompt.id
    stale_version_num = env["stale_version"].version

    # A soft-deleted version of an otherwise visible prompt.
    deleted_version_prompt = _seed_prompt(env["db"], env["ws"].id, "Dv.", versions=1)
    await env["db"].flush()
    deleted_version = PromptVersionModel(
        prompt_id=deleted_version_prompt.id,
        version=2,
        template="Deleted version.",
        variables=[],
        labels=[],
        workspace_id=env["ws"].id,
        deleted=True,
    )
    env["db"].add(deleted_version)
    await env["db"].commit()
    deleted_version_prompt_id = deleted_version_prompt.id

    before = await _snapshot(env["db"])
    async with _client_as(ctx) as http:
        cases = [
            (env["prompt"].id, 99, "missing"),
            (stale_prompt_id, stale_version_num, "workspace-mismatched"),
            (deleted_version_prompt_id, 2, "soft-deleted"),
        ]
        for prompt_id, version, _reason in cases:
            _assert_version_404(await http.get(f"{_BASE}/{prompt_id}/versions/{version}"))
            _assert_version_404(await http.post(f"{_BASE}/{prompt_id}/rollback/{version}"))
            _assert_version_404(
                await http.get(f"{_BASE}/{prompt_id}/diff", params={"from_version": 1, "to_version": version})
            )
            _assert_version_404(await http.get(f"{_BASE}/{prompt_id}/analytics", params={"version": version}))
            _assert_version_404(
                await http.get(f"{_BASE}/{prompt_id}/compare", params={"from_version": version, "to_version": 1})
            )
            _assert_version_404(await http.post(f"{_BASE}/{prompt_id}/versions/{version}/summary"))
    assert await _snapshot(env["db"]) == before


async def test_zero_uuid_workspace_is_a_legal_scope(env) -> None:
    """An explicit zero-UUID workspace scopes its own prompts, no others."""
    ctx = AuthContext(
        user_id=uuid.uuid4(),
        org_id=None,
        workspace_id=ZERO_WS,
        role=WorkspaceRole.VIEWER,
        auth_method="jwt",
        api_key_scope=None,
    )
    async with _client_as(ctx) as http:
        visible = await http.get(f"{_BASE}/{env['zero_prompt'].id}")
        assert visible.status_code == 200 and visible.json()["id"] == str(env["zero_prompt"].id)
        listing = await http.get(f"{_BASE}")
        assert listing.status_code == 200
        listed_ids = {item["id"] for item in listing.json()["items"]}
        assert str(env["zero_prompt"].id) in listed_ids
        assert str(env["prompt"].id) not in listed_ids
        cross = await http.get(f"{_BASE}/{env['prompt'].id}")
        _assert_prompt_404(cross)


async def test_list_versions_excludes_inconsistent_rows(env) -> None:
    """Legacy mismatched versions never appear in listings or reads."""
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        versions = await http.get(f"{_BASE}/{env['stale_prompt'].id}/versions")
        assert versions.status_code == 200
        assert [v["version"] for v in versions.json()] == [1]
        direct = await http.get(f"{_BASE}/{env['stale_prompt'].id}/versions/{env['stale_version'].version}")
        _assert_version_404(direct)


# ---------------------------------------------------------------------------
# Workspace binding of new versions
# ---------------------------------------------------------------------------


async def test_new_versions_inherit_prompt_workspace(env) -> None:
    """Create/update/rollback versions carry the parent workspace explicitly."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    ws_id = env["ws"].id
    async with _client_as(editor) as http:
        created = await http.post(f"{_BASE}", json={"name": f"inherit-{uuid.uuid4().hex[:6]}", "template": "v1"})
        assert created.status_code == 201
        prompt_id = created.json()["id"]
        updated = await http.put(f"{_BASE}/{prompt_id}", json={"template": "v2"})
        assert updated.status_code == 200
        rolled = await http.post(f"{_BASE}/{prompt_id}/rollback/1")
        assert rolled.status_code == 200
    rows = (await env["db"].execute(select(PromptVersionModel).order_by(PromptVersionModel.version))).scalars().all()
    for row in rows:
        if row.prompt_id == uuid.UUID(prompt_id):
            assert row.workspace_id == ws_id
    assert len([r for r in rows if r.prompt_id == uuid.UUID(prompt_id)]) == 3


# ---------------------------------------------------------------------------
# Protected labels
# ---------------------------------------------------------------------------


async def test_editor_cannot_create_protected_label(env) -> None:
    """Protected labels on create are admin-only; nothing persists on denial."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    admin = _member_as(env["ws"], WorkspaceRole.ADMIN)
    before = await _snapshot(env["db"])
    async with _client_as(editor) as http:
        denied = await http.post(
            f"{_BASE}",
            json={"name": f"pl-{uuid.uuid4().hex[:6]}", "template": "T", "labels": ["production"]},
        )
    _assert_forbidden(denied)
    assert await _snapshot(env["db"]) == before
    async with _client_as(admin) as http:
        allowed = await http.post(
            f"{_BASE}",
            json={"name": f"pl-{uuid.uuid4().hex[:6]}", "template": "T", "labels": ["production"]},
        )
    assert allowed.status_code == 201
    assert "production" in allowed.json()["version"]["labels"]


async def test_editor_cannot_carry_protected_label_by_omission(env) -> None:
    """Label carry-over to a new version requires admin, explicit or omitted."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    admin = _member_as(env["ws"], WorkspaceRole.ADMIN)
    before = await _snapshot(env["db"])
    async with _client_as(editor) as http:
        omitted = await http.put(f"{_BASE}/{env['labeled_prompt'].id}", json={"template": "editor rewrite."})
        explicit = await http.put(
            f"{_BASE}/{env['labeled_prompt'].id}",
            json={"template": "editor rewrite.", "labels": ["production"]},
        )
    _assert_forbidden(omitted)
    _assert_forbidden(explicit)
    assert await _snapshot(env["db"]) == before
    async with _client_as(admin) as http:
        allowed = await http.put(f"{_BASE}/{env['labeled_prompt'].id}", json={"template": "admin rewrite."})
    assert allowed.status_code == 200
    assert allowed.json()["current_version"] == 2
    assert "production" in allowed.json()["version"]["labels"]


async def test_editor_cannot_rollback_protected_label(env) -> None:
    """Rolling back onto a protected-labeled version requires admin."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    admin = _member_as(env["ws"], WorkspaceRole.ADMIN)
    before = await _snapshot(env["db"])
    async with _client_as(editor) as http:
        denied = await http.post(f"{_BASE}/{env['labeled_prompt'].id}/rollback/1")
    _assert_forbidden(denied)
    assert await _snapshot(env["db"]) == before
    async with _client_as(admin) as http:
        allowed = await http.post(f"{_BASE}/{env['labeled_prompt'].id}/rollback/1")
    assert allowed.status_code == 200
    assert allowed.json()["version"]["labels"] == ["production"]


async def test_editor_unprotected_labels_stay_editable(env) -> None:
    """Unprotected label operations remain EDITOR-capable."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        created = await http.post(
            f"{_BASE}",
            json={"name": f"ul-{uuid.uuid4().hex[:6]}", "template": "v1", "labels": ["staging"]},
        )
        assert created.status_code == 201
        updated = await http.put(
            f"{_BASE}/{created.json()['id']}",
            json={"template": "v2", "labels": ["development"]},
        )
        assert updated.status_code == 200
        assert updated.json()["version"]["labels"] == ["development"]
        rolled = await http.post(f"{_BASE}/{created.json()['id']}/rollback/1")
        assert rolled.status_code == 200
        assert rolled.json()["version"]["labels"] == ["staging"]


async def test_optimization_style_unlabeled_version_remains_manageable(env, llm_spy) -> None:
    """A legal labels=[] published version is fully manageable and readable."""
    prompt = _seed_prompt(env["db"], env["ws"].id, "Base.", versions=1)
    await env["db"].flush()
    env["db"].add(
        PromptVersionModel(
            prompt_id=prompt.id,
            version=2,
            template="Optimized {{q}}.",
            variables=["q"],
            labels=[],
            metadata_={"source": "prompt_optimization", "run_id": str(uuid.uuid4()), "candidate_id": str(uuid.uuid4())},
            workspace_id=env["ws"].id,
        )
    )
    prompt.current_version = 2
    await env["db"].commit()

    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        detail = await http.get(f"{_BASE}/{prompt.id}")
        assert detail.status_code == 200 and detail.json()["version"]["labels"] == []
        diff = await http.get(f"{_BASE}/{prompt.id}/diff", params={"from_version": 1, "to_version": 2})
        assert diff.status_code == 200
    async with _client_as(editor) as http:
        update = await http.put(f"{_BASE}/{prompt.id}", json={"template": "Post-opt edit."})
        assert update.status_code == 200 and update.json()["current_version"] == 3
        summary = await http.post(f"{_BASE}/{prompt.id}/versions/2/summary")
        assert summary.status_code == 200
    assert len(llm_spy["calls"]) == 1


@pytest.mark.parametrize("operation", ["update", "rollback", "delete"])
async def test_write_rejects_move_after_service_read(env, monkeypatch, operation) -> None:
    """A service read cannot authorize a later write into a foreign workspace."""
    prompt_id = env["prompt"].id
    method = "_get_prompt" if operation == "delete" else "_get_version"
    original = getattr(PromptService, method)
    armed = False

    async def move_after_read(self, *args, **kwargs):
        nonlocal armed
        row = await original(self, *args, **kwargs)
        if row is not None and not armed:
            armed = True
            async with shared_session_factory() as other:
                prompt = await other.get(PromptModel, prompt_id)
                prompt.workspace_id = env["other_ws"].id
                await other.commit()
        return row

    monkeypatch.setattr(PromptService, method, move_after_read)
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        if operation == "update":
            response = await http.put(f"{_BASE}/{prompt_id}", json={"template": "must not land"})
        elif operation == "rollback":
            response = await http.post(f"{_BASE}/{prompt_id}/rollback/1")
        else:
            response = await http.delete(f"{_BASE}/{prompt_id}")
    assert armed
    assert response.status_code == 404, response.text
    snapshot = await _snapshot(env["db"])
    prompt = next(row for row in snapshot["prompts"] if row["id"] == prompt_id)
    assert prompt["current_version"] == 2 and not prompt["deleted"]
    assert sum(row["prompt_id"] == prompt_id for row in snapshot["prompt_versions"]) == 2


@pytest.mark.parametrize("operation", ["update", "rollback"])
async def test_version_claim_rejects_source_move_or_pointer_race(env, monkeypatch, operation) -> None:
    """A source version moving after its read cannot be copied forward."""
    original = PromptService._get_version
    armed = False

    async def move_after_read(self, *args, **kwargs):
        nonlocal armed
        row = await original(self, *args, **kwargs)
        if row is not None and not armed:
            armed = True
            async with shared_session_factory() as other:
                version = await other.get(PromptVersionModel, row.id)
                version.workspace_id = env["other_ws"].id
                await other.commit()
        return row

    monkeypatch.setattr(PromptService, "_get_version", move_after_read)
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        if operation == "update":
            response = await http.put(f"{_BASE}/{env['prompt'].id}", json={"template": "no copy"})
        else:
            response = await http.post(f"{_BASE}/{env['prompt'].id}/rollback/1")
    assert response.status_code == 404, response.text
    snapshot = await _snapshot(env["db"])
    assert next(row for row in snapshot["prompts"] if row["id"] == env["prompt"].id)["current_version"] == 2
    assert sum(row["prompt_id"] == env["prompt"].id for row in snapshot["prompt_versions"]) == 2


@pytest.mark.parametrize("entry", ["list", "versions", "label"])
async def test_lists_never_return_cached_foreign_values(env, entry) -> None:
    """SQL scoping must also refresh objects already present in the session."""
    prompt = _seed_prompt(env["db"], env["other_ws"].id, "PRIVATE-CACHED", labels=["cache"])
    prompt.name = "PRIVATE-NAME"
    await env["db"].commit()
    version = (
        await env["db"].execute(
            select(PromptVersionModel).where(
                PromptVersionModel.prompt_id == prompt.id,
            )
        )
    ).scalar_one()
    async with shared_session_factory() as other:
        current = await other.get(PromptModel, prompt.id)
        current.workspace_id = env["ws"].id
        current.name = "PUBLIC-NAME"
        child = await other.get(PromptVersionModel, version.id)
        child.workspace_id = env["ws"].id
        child.template = "PUBLIC-CURRENT"
        await other.commit()
    path = (
        _BASE
        if entry == "list"
        else f"{_BASE}/{prompt.id}/versions"
        if entry == "versions"
        else f"{_BASE}/by-label/cache"
    )
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(path)
    assert response.status_code == 200, response.text
    assert "PRIVATE" not in response.text
    assert ("PUBLIC-NAME" if entry == "list" else "PUBLIC-CURRENT") in response.text


async def test_analytics_excludes_contradictory_session_agent(env) -> None:
    """A local session linked to a foreign agent is not attribution proof."""
    foreign_agent = (
        await env["db"].execute(
            select(AgentModel).where(
                AgentModel.workspace_id == env["other_ws"].id,
            )
        )
    ).scalar_one()
    session = SessionModel(agent_id=foreign_agent.id, workspace_id=env["ws"].id)
    env["db"].add(session)
    await env["db"].flush()
    env["db"].add(
        _seed_trace(
            prompt_id=env["prompt"].id,
            version=2,
            session_id=session.id,
            agent_id=None,
            tokens=9999,
        )
    )
    await env["db"].commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(f"{_BASE}/{env['prompt'].id}/analytics", params={"version": 2})
    assert response.status_code == 200, response.text
    assert response.json()["total_calls"] == 2
    assert response.json()["total_tokens"] == 15


async def test_analytics_ignores_invalid_version_markers(env) -> None:
    """Malformed metadata never aliases an integer version or breaks analytics."""
    for marker in (True, 1.0, "bad", "1.2", "", {"version": 1}):
        trace = _seed_trace(
            prompt_id=env["prompt"].id,
            version=1,
            session_id=env["session_row"].id,
            agent_id=env["agent"].id,
            tokens=9999,
        )
        trace.metadata_ = {"prompt_id": str(env["prompt"].id), "prompt_version": marker}
        env["db"].add(trace)
    await env["db"].commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(f"{_BASE}/{env['prompt'].id}/analytics", params={"version": 1})
    assert response.status_code == 200, response.text
    assert response.json()["total_calls"] == 0


@pytest.mark.parametrize("operation", ["update", "rollback"])
async def test_competing_version_advance_returns_conflict_without_partial_write(env, monkeypatch, operation) -> None:
    """An intervening committed version never becomes a duplicate insert."""
    original = PromptService._get_version
    armed = False

    async def advance_after_read(self, *args, **kwargs):
        nonlocal armed
        row = await original(self, *args, **kwargs)
        if row is not None and not armed:
            armed = True
            async with shared_session_factory() as other:
                prompt = await other.get(PromptModel, env["prompt"].id)
                prompt.current_version = 3
                other.add(
                    PromptVersionModel(
                        prompt_id=prompt.id,
                        version=3,
                        workspace_id=env["ws"].id,
                        template="Concurrent version",
                        variables=[],
                        labels=[],
                    )
                )
                await other.commit()
        return row

    monkeypatch.setattr(PromptService, "_get_version", advance_after_read)
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        if operation == "update":
            response = await http.put(f"{_BASE}/{env['prompt'].id}", json={"template": "not version 3"})
        else:
            response = await http.post(f"{_BASE}/{env['prompt'].id}/rollback/1")
    assert response.status_code == 409, response.text
    snapshot = await _snapshot(env["db"])
    assert sum(row["prompt_id"] == env["prompt"].id for row in snapshot["prompt_versions"]) == 3


@pytest.mark.parametrize("operation", ["get", "update"])
async def test_current_version_visibility_precedes_role(env, operation) -> None:
    """A corrupt current-version pointer is invisible even to a roleless caller."""
    env["stale_prompt"].current_version = 2
    await env["db"].commit()
    async with _client_as(_member_as(env["ws"], None)) as http:
        path = f"{_BASE}/{env['stale_prompt'].id}"
        response = await http.get(path) if operation == "get" else await http.put(path, json={"template": "no"})
    assert response.status_code == 404, response.text


async def test_protected_label_only_payload_is_not_silently_accepted(env) -> None:
    """Omitting the template must not skip the protected-label permission check."""
    before = await _snapshot(env["db"])
    async with _client_as(_member_as(env["ws"], WorkspaceRole.EDITOR)) as http:
        response = await http.put(f"{_BASE}/{env['prompt'].id}", json={"labels": ["production"]})
    assert response.status_code == 403, response.text
    assert await _snapshot(env["db"]) == before


async def test_by_label_matches_a_member_of_multiple_labels(env) -> None:
    """Lookup uses exact array membership, independent of array formatting."""
    prompt = _seed_prompt(env["db"], env["ws"].id, "Multiple labels", labels=["dev", "shared", "qa"])
    await env["db"].commit()
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(f"{_BASE}/by-label/shared")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(prompt.id)


async def test_version_listing_rechecks_parent_in_content_query(env, monkeypatch) -> None:
    """A parent moving after its read must not expose newly foreign content."""
    original = PromptService._get_prompt

    async def move_after_read(self, *args, **kwargs):
        prompt = await original(self, *args, **kwargs)
        async with shared_session_factory() as other:
            current = await other.get(PromptModel, prompt.id)
            current.workspace_id = env["other_ws"].id
            versions = (
                (
                    await other.execute(
                        select(PromptVersionModel).where(
                            PromptVersionModel.prompt_id == prompt.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for version in versions:
                version.template = "NEW-FOREIGN-CONTENT"
            await other.commit()
        return prompt

    monkeypatch.setattr(PromptService, "_get_prompt", move_after_read)
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        response = await http.get(f"{_BASE}/{env['prompt'].id}/versions")
    assert response.status_code == 404, response.text
    assert "NEW-FOREIGN-CONTENT" not in response.text


@pytest.mark.parametrize("operation", ["update", "rollback", "delete"])
async def test_file_sqlite_rejects_move_on_an_independent_connection(
    governance_session_factory, monkeypatch, operation
):
    """A caller may safely commit after a rejected write; no partial write exists."""
    from hecate.core.database import Base
    from hecate.models.prompt import PromptUpdateSchema

    factory = governance_session_factory
    async with factory.kw["bind"].begin() as connection:
        await connection.run_sync(
            lambda sync: Base.metadata.create_all(
                sync,
                tables=[
                    PromptModel.__table__,
                    PromptVersionModel.__table__,
                ],
            )
        )
    workspace_id, foreign_workspace_id = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        prompt = PromptModel(name="independent-race", workspace_id=workspace_id, current_version=1)
        session.add(prompt)
        await session.flush()
        session.add(
            PromptVersionModel(
                prompt_id=prompt.id,
                version=1,
                workspace_id=workspace_id,
                template="Original",
                variables=[],
                labels=[],
            )
        )
        await session.commit()
        prompt_id = prompt.id

    method = "_get_prompt" if operation == "delete" else "_get_version"
    original = getattr(PromptService, method)
    armed = False

    async def move_after_read(self, *args, **kwargs):
        nonlocal armed
        row = await original(self, *args, **kwargs)
        if not armed:
            armed = True
            async with factory() as other:
                current = await other.get(PromptModel, prompt_id)
                current.workspace_id = foreign_workspace_id
                await other.commit()
        return row

    monkeypatch.setattr(PromptService, method, move_after_read)
    async with factory() as caller:
        service = PromptService(caller)
        with pytest.raises(LookupError):
            if operation == "update":
                await service.update_prompt(prompt_id, PromptUpdateSchema(template="No write"), workspace_id)
            elif operation == "rollback":
                await service.rollback_to_version(prompt_id, 1, workspace_id)
            else:
                await service.delete_prompt(prompt_id, workspace_id)
        await caller.commit()
    async with factory() as reader:
        current = await reader.get(PromptModel, prompt_id)
        assert current is not None
        assert current.workspace_id == foreign_workspace_id and not current.deleted and current.current_version == 1
        versions = (
            (
                await reader.execute(
                    select(PromptVersionModel).where(
                        PromptVersionModel.prompt_id == prompt_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(versions) == 1


# ---------------------------------------------------------------------------
# By-label scoping
# ---------------------------------------------------------------------------


async def test_by_label_is_workspace_scoped_before_ordering(env) -> None:
    """A newer foreign labeled version never shadows the caller's own."""
    db = env["db"]
    local = _seed_prompt(db, env["ws"].id, "Local deploy.", labels=["deploy"], versions=1)
    foreign = _seed_prompt(db, env["other_ws"].id, "Foreign deploy.", labels=["deploy"], versions=1)
    await db.flush()
    # Make the foreign version strictly newer.
    local_v = (
        await db.execute(select(PromptVersionModel).where(PromptVersionModel.prompt_id == local.id))
    ).scalar_one()
    foreign_v = (
        await db.execute(select(PromptVersionModel).where(PromptVersionModel.prompt_id == foreign.id))
    ).scalar_one()
    foreign_v.created_at = datetime.now(UTC) + timedelta(seconds=60)
    local_v_id = local_v.id
    await db.commit()

    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        found = await http.get(f"{_BASE}/by-label/deploy")
        assert found.status_code == 200
        assert found.json()["id"] == str(local.id)
        assert found.json()["version"]["id"] == str(local_v_id)


async def test_by_label_skips_deleted_parent_and_mismatched_versions(env) -> None:
    """Deleted parents and workspace-mismatched labeled versions 404."""
    db = env["db"]
    _seed_prompt(db, env["ws"].id, "Dead deploy.", labels=["ghost"], versions=1, deleted=True)
    await db.commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        ghost = await http.get(f"{_BASE}/by-label/ghost")
    _assert_not_found(ghost, "No prompt with label 'ghost'")

    # The seeded stale_version carries label "deploy" but its workspace
    # disagrees with the parent prompt — invisible from either side.
    async with _client_as(_member_as(env["ws"], WorkspaceRole.VIEWER)) as http:
        from_owner = await http.get(f"{_BASE}/by-label/deploy")
    _assert_not_found(from_owner, "No prompt with label 'deploy'")
    async with _client_as(_member_as(env["other_ws"], WorkspaceRole.VIEWER)) as http:
        from_version_ws = await http.get(f"{_BASE}/by-label/deploy")
    _assert_not_found(from_version_ws, "No prompt with label 'deploy'")


# ---------------------------------------------------------------------------
# Analytics boundary
# ---------------------------------------------------------------------------


async def test_analytics_counts_only_workspace_visible_traces(env) -> None:
    """Authorized associations return real numbers; every bad shape adds zero."""
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        analytics = await http.get(f"{_BASE}/{env['prompt'].id}/analytics", params={"version": 2})
    assert analytics.status_code == 200, analytics.text
    body = analytics.json()
    # Two authorized traces: int-marker via session, string-marker via agent.
    assert body["total_calls"] == 2
    assert body["total_tokens"] == 15
    assert body["avg_latency_ms"] == 200.0
    assert body["error_rate"] == 0.5
    assert body["total_cost"] == 0.0
    assert sum(entry["calls"] for entry in body["daily_breakdown"]) == 2
    assert len(body["daily_breakdown"]) == 1


async def test_analytics_authorized_via_session_and_agent_associations(env) -> None:
    """Each authorized association shape proves the positive path alone."""
    prompt = _seed_prompt(env["db"], env["ws"].id, "Assoc.", versions=1)
    await env["db"].flush()
    db = env["db"]
    s = SessionModel(agent_id=env["agent"].id, workspace_id=env["ws"].id)
    db.add(s)
    await db.flush()
    db.add_all(
        [
            _seed_trace(prompt_id=prompt.id, version=1, session_id=s.id, agent_id=None, tokens=7),
            _seed_trace(prompt_id=prompt.id, version=1, session_id=s.id, agent_id=env["agent"].id, tokens=3),
        ]
    )
    await db.commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        via_session = await http.get(f"{_BASE}/{prompt.id}/analytics", params={"version": 1})
    assert via_session.status_code == 200
    assert via_session.json()["total_calls"] == 2
    assert via_session.json()["total_tokens"] == 10


async def test_analytics_foreign_prompt_metadata_never_authorizes(env) -> None:
    """Foreign traces carrying this prompt's metadata stay invisible."""
    prompt = _seed_prompt(env["db"], env["ws"].id, "Island.", versions=1)
    await env["db"].flush()
    other_session = SessionModel(agent_id=env["agent"].id, workspace_id=env["other_ws"].id)
    env["db"].add(other_session)
    await env["db"].flush()
    env["db"].add(_seed_trace(prompt_id=prompt.id, version=1, session_id=other_session.id, agent_id=None))
    await env["db"].commit()
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        analytics = await http.get(f"{_BASE}/{prompt.id}/analytics", params={"version": 1})
    assert analytics.status_code == 200
    assert analytics.json()["total_calls"] == 0
    assert analytics.json()["daily_breakdown"] == []


async def test_compare_validates_both_versions_before_stats(env) -> None:
    """A foreign second version 404s before any statistics run."""
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        denied = await http.get(
            f"{_BASE}/{env['stale_prompt'].id}/compare",
            params={"from_version": 1, "to_version": env["stale_version"].version},
        )
        _assert_version_404(denied)
        ok = await http.get(f"{_BASE}/{env['prompt'].id}/compare", params={"from_version": 1, "to_version": 2})
        assert ok.status_code == 200
        body = ok.json()
        assert body["from_analytics"]["total_calls"] == 0
        assert body["to_analytics"]["total_calls"] == 2
        assert body["deltas"]["total_calls"] == 2


# ---------------------------------------------------------------------------
# Summary gating
# ---------------------------------------------------------------------------


async def test_summary_shortcuts_require_visibility_and_role(env, llm_spy) -> None:
    """Initial-version and no-change shortcuts stay behind the boundary."""
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)

    same = _seed_prompt(env["db"], env["ws"].id, "Same.", versions=2)
    await env["db"].flush()
    for row in (
        await env["db"].execute(select(PromptVersionModel).where(PromptVersionModel.prompt_id == same.id))
    ).scalars():
        row.template = "Identical template."
    await env["db"].commit()

    async with _client_as(editor) as http:
        foreign_initial = await http.post(f"{_BASE}/{env['foreign_prompt'].id}/versions/1/summary")
        _assert_version_404(foreign_initial)
        missing_prev = await http.post(f"{_BASE}/{env['stale_prompt'].id}/versions/2/summary")
        _assert_version_404(missing_prev)
        initial = await http.post(f"{_BASE}/{env['prompt'].id}/versions/1/summary")
        assert initial.status_code == 200 and initial.json()["is_initial"] is True
        no_change = await http.post(f"{_BASE}/{same.id}/versions/2/summary")
        assert no_change.status_code == 200 and "no template changes" in no_change.json()["summary"].lower()
    assert llm_spy["calls"] == []

    async with _client_as(viewer) as http:
        denied = await http.post(f"{_BASE}/{env['prompt'].id}/versions/2/summary")
    _assert_forbidden(denied)
    assert llm_spy["calls"] == []


async def test_denied_summary_never_reaches_llm_or_fallback(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Denied requests skip the model call and its failure fallback entirely."""
    from hecate_llm.service import LLMService

    async def exploding(self, messages, model=None, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("denied requests must not call the model")

    monkeypatch.setattr(LLMService, "chat", exploding)
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        denied = await http.post(f"{_BASE}/{env['prompt'].id}/versions/2/summary")
    _assert_forbidden(denied)


# ---------------------------------------------------------------------------
# Write-path rechecks after the API precheck
# ---------------------------------------------------------------------------


async def test_update_rechecks_binding_after_precheck(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A prompt moved after the precheck is refused, not followed."""
    prompt_id = env["prompt"].id
    other_ws_id = env["other_ws"].id

    async def move(session: AsyncSession) -> None:
        row = await session.get(PromptModel, prompt_id)
        row.workspace_id = other_ws_id

    _interpose(monkeypatch, PromptService, "update_prompt", move)
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.put(f"{_BASE}/{prompt_id}", json={"template": "should not land."})
    _assert_prompt_404(response)
    db = env["db"]
    db.expire_all()
    row = await db.get(PromptModel, prompt_id)
    assert row.workspace_id == other_ws_id  # the test's own move, not the API's
    versions = (
        (await db.execute(select(PromptVersionModel).where(PromptVersionModel.prompt_id == prompt_id))).scalars().all()
    )
    assert len(versions) == 2 and row.current_version == 2


async def test_delete_rechecks_binding_after_precheck(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A prompt moved after the precheck is never deleted by the stale caller."""
    prompt_id = env["prompt"].id
    other_ws_id = env["other_ws"].id

    async def move(session: AsyncSession) -> None:
        row = await session.get(PromptModel, prompt_id)
        row.workspace_id = other_ws_id

    _interpose(monkeypatch, PromptService, "delete_prompt", move)
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.delete(f"{_BASE}/{prompt_id}")
    _assert_prompt_404(response)
    db = env["db"]
    db.expire_all()
    row = await db.get(PromptModel, prompt_id)
    assert row.deleted is False and row.workspace_id == other_ws_id


async def test_rollback_rechecks_target_after_precheck(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A target version deleted after the precheck rolls back nothing."""
    prompt_id = env["prompt"].id
    stmt = select(PromptVersionModel).where(PromptVersionModel.prompt_id == prompt_id, PromptVersionModel.version == 1)
    target_version_id = (await env["db"].execute(stmt)).scalar_one().id

    async def remove(session: AsyncSession) -> None:
        row = await session.get(PromptVersionModel, target_version_id)
        row.deleted = True

    _interpose(monkeypatch, PromptService, "rollback_to_version", remove)
    editor = _member_as(env["ws"], WorkspaceRole.EDITOR)
    async with _client_as(editor) as http:
        response = await http.post(f"{_BASE}/{prompt_id}/rollback/1")
    _assert_version_404(response)
    db = env["db"]
    db.expire_all()
    row = await db.get(PromptModel, prompt_id)
    assert row.current_version == 2
    versions = (
        (await db.execute(select(PromptVersionModel).where(PromptVersionModel.prompt_id == prompt_id))).scalars().all()
    )
    assert len(versions) == 2


async def test_visible_prompt_read_rechecks_binding(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even reads re-read the binding: a post-probe move 404s on content."""
    prompt_id = env["prompt"].id
    other_ws_id = env["other_ws"].id

    async def move(session: AsyncSession) -> None:
        row = await session.get(PromptModel, prompt_id)
        row.workspace_id = other_ws_id

    _interpose(monkeypatch, PromptService, "get_prompt", move)
    viewer = _member_as(env["ws"], WorkspaceRole.VIEWER)
    async with _client_as(viewer) as http:
        response = await http.get(f"{_BASE}/{prompt_id}")
    _assert_prompt_404(response)


# ---------------------------------------------------------------------------
# Preserved success semantics
# ---------------------------------------------------------------------------


async def test_success_semantics_preserved(env, llm_spy) -> None:
    """Established success shapes survive the boundary unchanged."""
    admin = _member_as(env["ws"], WorkspaceRole.ADMIN)
    async with _client_as(admin) as http:
        created = await http.post(
            f"{_BASE}",
            json={"name": f"ok-{uuid.uuid4().hex[:6]}", "template": "One {{x}}.", "commit_message": "init"},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["current_version"] == 1 and body["version"]["template"] == "One {{x}}."
        assert body["workspace_id"] == str(env["ws"].id)
        assert body["version"]["commit_message"] == "init"

        prompt_id = body["id"]
        updated = await http.put(f"{_BASE}/{prompt_id}", json={"template": "Two {{x}}.", "commit_message": "v2"})
        assert updated.status_code == 200 and updated.json()["current_version"] == 2

        diff = await http.get(f"{_BASE}/{prompt_id}/diff", params={"from_version": 1, "to_version": 2})
        assert diff.status_code == 200
        diff_body = diff.json()
        assert diff_body["from_commit_message"] == "init" and diff_body["to_commit_message"] == "v2"
        assert any(entry["type"] == "added" for entry in diff_body["diff_entries"])

        rolled = await http.post(f"{_BASE}/{prompt_id}/rollback/1")
        assert rolled.status_code == 200
        assert rolled.json()["version"]["commit_message"] == "Rollback to version 1"

        summary = await http.post(f"{_BASE}/{prompt_id}/versions/2/summary")
        assert summary.status_code == 200
        assert summary.json()["summary"].startswith("stubbed summary")
        assert summary.json()["diff_stats"]["token_delta"] is not None
    assert len(llm_spy["calls"]) == 1
