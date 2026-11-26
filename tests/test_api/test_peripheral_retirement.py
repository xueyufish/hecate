"""Retirement acceptance for the M1-T1 legacy peripheral modules.

The un-gated peripheral surfaces are physically removed: the legacy skill
self-evolution loop (``/api/self-evolution/*``, ``SKILL_EVOLUTION_*``), the
dataset synthesis entries (``/api/evaluation/datasets/synthesize`` and
``/api/evaluation/synthesis-jobs/*``), the fine-tuning management API
(``/api/fine-tuning/*``) and the intent-package management API
(``/api/intent-packages/*``). This module pins the retirement contract on
the in-memory test database:

* the real application lifespan completes and ``/health/startup`` reports 200;
* none of the retired paths survive in the OpenAPI document;
* legacy requests never reach a legacy handler;
* retained routes that share prefixes with the retired ones keep serving —
  especially ``/api/evaluation/datasets*``, workflow create/update, and the
  manual skill surface.

Retained behaviour for existing data: skill usage events keep their
read/write paths in the skill loader, published workflow graphs keep
resolving intent evidence through ``IntentEvidencePort``, and synthesized
samples stay readable as ordinary dataset items with ``strategy:*`` tags.
History tables converge in M1-T3.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

# Retired prefixes — no path under them may survive in OpenAPI.
_RETIRED_PREFIXES = (
    "/api/self-evolution",
    "/api/evaluation/synthesis-jobs",
    "/api/fine-tuning",
    "/api/intent-packages",
)

# Representative retired operations (concrete templates as OpenAPI lists them).
_RETIRED_OPERATIONS = [
    ("GET", "/api/self-evolution/inputs"),
    ("POST", "/api/self-evolution/inputs/harvest"),
    ("GET", "/api/self-evolution/runs"),
    ("GET", "/api/self-evolution/candidates"),
    ("GET", "/api/self-evolution/candidates/{candidate_id}"),
    ("POST", "/api/self-evolution/candidates/{candidate_id}/review"),
    ("POST", "/api/self-evolution/candidates/{candidate_id}/unpublish"),
    ("GET", "/api/self-evolution/candidates/{candidate_id}/lineage"),
    ("GET", "/api/self-evolution/skills/{skill_name}/usage"),
    ("POST", "/api/evaluation/datasets/synthesize"),
    ("GET", "/api/evaluation/synthesis-jobs/{job_id}"),
    ("GET", "/api/fine-tuning/datasets"),
    ("POST", "/api/fine-tuning/datasets/upload"),
    ("GET", "/api/fine-tuning/jobs"),
    ("POST", "/api/fine-tuning/jobs"),
    ("GET", "/api/fine-tuning/jobs/{job_id}"),
    ("POST", "/api/fine-tuning/jobs/{job_id}/cancel"),
    ("POST", "/api/fine-tuning/jobs/{job_id}/deploy"),
    ("GET", "/api/intent-packages"),
    ("POST", "/api/intent-packages"),
    ("POST", "/api/intent-packages/{package_id}/versions/{version_id}/publish"),
]


def _concrete(template: str) -> str:
    for placeholder in ("run_id", "candidate_id", "skill_name", "job_id", "package_id", "version_id"):
        template = template.replace("{" + placeholder + "}", str(uuid.uuid4()))
    return template


@pytest.mark.parametrize("maintenance_enabled", [False, True])
async def test_real_lifespan_completes_and_startup_probe_reports_ready(monkeypatch, maintenance_enabled) -> None:
    """The minimal app lifespan boots on the in-memory database and serves.

    Mirrors ``test_prompt_optimization_retirement.py``: tracing stays off so
    the global tracer provider cannot leak into later tests, and composition
    DB traffic is redirected to the in-memory test engine so the lifespan
    runs against the session schema. Shutdown completes cleanly, stopping
    any maintenance scheduler the lifespan started.
    """
    from hecate.core.config import settings
    from hecate.ops.maintenance.compliance_checker import ComplianceCheckerAgent
    from hecate.ops.maintenance.garbage_collector import GarbageCollectorAgent

    monkeypatch.setattr(settings, "TRACING_ENABLED", False)
    monkeypatch.setattr(settings, "TRACE_DB_EXPORT_ENABLED", False)
    monkeypatch.setattr(settings, "META_AGENTS_ENABLED", maintenance_enabled)
    compliance_started = asyncio.Event()
    collection_started = asyncio.Event()

    async def check_compliance(_self):
        compliance_started.set()

    async def collect_garbage(_self, _session):
        collection_started.set()

    monkeypatch.setattr(ComplianceCheckerAgent, "run", check_compliance)
    monkeypatch.setattr(GarbageCollectorAgent, "run", collect_garbage)

    import hecate.core.database as database
    from hecate.main import app
    from tests.conftest import test_engine, test_session_factory

    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    async with app.router.lifespan_context(app):
        scheduler = app.state.meta_scheduler
        if maintenance_enabled:
            assert scheduler is not None
            await asyncio.wait_for(asyncio.gather(compliance_started.wait(), collection_started.wait()), timeout=5)
            scheduled_tasks = list(scheduler._tasks)
            assert len(scheduled_tasks) == 2
        else:
            assert scheduler is None
            scheduled_tasks = []
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            probe = await http.get("/health/startup")
            assert probe.status_code == 200
            assert probe.json() == {"status": "started", "startup_complete": True}
    assert all(task.done() for task in scheduled_tasks)


def test_openapi_omits_every_retired_peripheral_path() -> None:
    """The OpenAPI document no longer lists any retired peripheral path."""
    from hecate.main import app

    paths = app.openapi()["paths"]
    for prefix in _RETIRED_PREFIXES:
        assert [path for path in paths if path.startswith(prefix)] == [], prefix
    for _method, template in _RETIRED_OPERATIONS:
        assert template not in paths


@pytest.mark.parametrize(("method", "template"), _RETIRED_OPERATIONS)
async def test_legacy_peripheral_requests_cannot_reach_retired_handlers(client, method, template) -> None:
    """Every retired GET and POST entry is unreachable.

    Dedicated prefixes answer 404. The one shared-prefix entry
    (``/api/evaluation/datasets/synthesize``) resolves to the kept
    ``{dataset_id}`` path pattern and answers 405 — either way the request
    can no longer reach a legacy handler.
    """
    response = await client.request(method, _concrete(template), json={} if method == "POST" else None)
    assert response.status_code == (405 if template == "/api/evaluation/datasets/synthesize" else 404)


async def test_synthesize_under_dataset_prefix_is_gone_but_datasets_serve(client) -> None:
    """The shared /api/evaluation/datasets prefix keeps serving retained routes.

    ``POST /api/evaluation/datasets/synthesize`` used to shadow the
    ``{dataset_id}`` segment; it is gone — the request now resolves to the
    kept ``{dataset_id}`` path pattern and its method guard (405) instead
    of a synthesis handler — while the ordinary dataset listing keeps
    answering.
    """
    gone = await client.post("/api/evaluation/datasets/synthesize", json={})
    assert gone.status_code == 405
    datasets = await client.get("/api/evaluation/datasets")
    assert datasets.status_code == 200
    job = await client.get(f"/api/evaluation/synthesis-jobs/{uuid.uuid4()}")
    assert job.status_code == 404


async def test_retained_studio_and_skill_entries_still_serve(client) -> None:
    """Workflow, skill, prompt and evaluator entries are untouched."""
    workflows = await client.get("/api/workflows")
    assert workflows.status_code == 200
    skills = await client.get("/api/skills")
    assert skills.status_code == 200
    prompts = await client.get("/api/prompts")
    assert prompts.status_code == 200
    evaluators = await client.get("/api/evaluation/evaluators")
    assert evaluators.status_code == 200 and evaluators.json()["total"] > 0
