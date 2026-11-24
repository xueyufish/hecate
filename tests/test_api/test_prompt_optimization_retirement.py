"""Retirement acceptance for the legacy prompt self-optimization surface (6.19).

The dataset-driven prompt optimization loop is physically removed: its
in-process runner, the ``/api/prompt-optimization`` router and the
``PROMPT_OPTIMIZATION_ENABLED`` flag no longer exist. This module pins the
retirement contract on the in-memory test database:

* the real application lifespan completes and ``/health/startup`` reports 200;
* no ``/api/prompt-optimization/*`` path survives in the OpenAPI document;
* every legacy GET/POST entry answers 404;
* ordinary prompt, evaluation and skill entries keep serving.

Historical optimization-published prompt versions (``metadata_.source ==
"prompt_optimization"``) stay readable, comparable and rollback-able through
the prompt surface — covered in
``test_prompt_management_access_boundary.py``. The dedicated optimization ORM
and history tables are kept for M1-T3 to converge.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

_LEGACY_PREFIX = "/api/prompt-optimization"

# The seven retired paths / eight operations the main OpenAPI used to list.
_LEGACY_OPERATIONS = [
    ("GET", "/api/prompt-optimization/runs"),
    ("POST", "/api/prompt-optimization/runs"),
    ("GET", "/api/prompt-optimization/runs/{run_id}"),
    ("POST", "/api/prompt-optimization/runs/{run_id}/cancel"),
    ("GET", "/api/prompt-optimization/runs/{run_id}/candidates"),
    ("GET", "/api/prompt-optimization/candidates/{candidate_id}"),
    ("POST", "/api/prompt-optimization/candidates/{candidate_id}/approve"),
    ("POST", "/api/prompt-optimization/candidates/{candidate_id}/reject"),
]


def _concrete(template: str) -> str:
    return template.replace("{run_id}", str(uuid.uuid4())).replace("{candidate_id}", str(uuid.uuid4()))


async def test_real_lifespan_completes_and_startup_probe_reports_ready(monkeypatch) -> None:
    """The minimal app lifespan boots on the in-memory database and serves.

    Mirrors ``test_chat_session_state_wiring.py``: tracing stays off so the
    global tracer provider cannot leak into later tests, and composition DB
    traffic is redirected to the in-memory test engine so the lifespan runs
    against the session schema.
    """
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "TRACING_ENABLED", False)
    monkeypatch.setattr(settings, "TRACE_DB_EXPORT_ENABLED", False)

    import hecate.core.database as database
    from hecate.main import app
    from tests.conftest import test_engine, test_session_factory

    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            probe = await http.get("/health/startup")
            assert probe.status_code == 200
            assert probe.json() == {"status": "started", "startup_complete": True}
            legacy_get = await http.get(f"{_LEGACY_PREFIX}/runs")
            legacy_post = await http.post(f"{_LEGACY_PREFIX}/runs", json={})
    assert legacy_get.status_code == 404
    assert legacy_post.status_code == 404


def test_openapi_omits_every_legacy_optimization_path() -> None:
    """The OpenAPI document no longer lists any prompt optimization path."""
    from hecate.main import app

    paths = app.openapi()["paths"]
    assert [path for path in paths if path.startswith(_LEGACY_PREFIX)] == []
    for _method, template in _LEGACY_OPERATIONS:
        assert template not in paths


@pytest.mark.parametrize(("method", "template"), _LEGACY_OPERATIONS)
async def test_legacy_optimization_requests_return_404(client, method, template) -> None:
    """Every retired GET and POST entry answers 404, flag or not."""
    response = await client.request(method, _concrete(template), json={} if method == "POST" else None)
    assert response.status_code == 404


async def test_prompt_evaluation_and_skill_entries_still_serve(client) -> None:
    """Ordinary prompt, evaluation and skill entries are untouched."""
    prompts = await client.get("/api/prompts")
    assert prompts.status_code == 200
    evaluators = await client.get("/api/evaluation/evaluators")
    assert evaluators.status_code == 200 and evaluators.json()["total"] > 0
    datasets = await client.get("/api/evaluation/datasets")
    assert datasets.status_code == 200
    skills = await client.get("/api/skills")
    assert skills.status_code == 200
