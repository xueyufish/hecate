"""FastAPI application entry point for Hecate Agent Platform.

Initializes the FastAPI application with:
- CORS middleware (allows all origins for development)
- Unified error handling with consistent JSON error format
- Lifespan events for database connection management
- Health check endpoint at ``GET /health``
- Route registration for ``/v1`` (OpenAI compatible) and ``/api`` (management)
"""

from __future__ import annotations

import logging
import signal
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from hecate.api.management.budget import router as budget_router
from hecate.api.management.collaboration_patterns import router as collaboration_patterns_router
from hecate.api.management.conversations import router as conversations_router
from hecate.api.management.replay import router as replay_router
from hecate.api.management.traces import router as traces_router
from hecate.channel.api.a2a import router as a2a_management_router
from hecate.channel.api.v1.agents import router as agent_chat_router
from hecate.channel.api.v1.chat import router as chat_router
from hecate.channel.api.v1.models import router as models_router
from hecate.channel.management.alerts import (
    channels_router as alert_channels_router,
)
from hecate.channel.management.alerts import (
    escalation_policies_router as alert_escalation_policies_router,
)
from hecate.channel.management.alerts import (
    events_router as alert_events_router,
)
from hecate.channel.management.alerts import (
    rules_router as alert_rules_router,
)
from hecate.channel.management.alerts import (
    silences_router as alert_silences_router,
)
from hecate.core.api.feature_flags import router as feature_flags_router
from hecate.core.api.i18n import router as i18n_router
from hecate.core.config import settings as _settings
from hecate.core.database import engine
from hecate.core.middleware.audit import AuditMiddleware
from hecate.enterprise.api.api_keys import router as api_keys_router
from hecate.enterprise.api.model_providers import router as model_providers_router
from hecate.ops.api.agent_health import router as agent_health_router
from hecate.ops.api.audit import router as audit_router
from hecate.ops.api.conversation_analytics import router as conversation_analytics_router
from hecate.ops.api.costs import router as costs_router
from hecate.ops.api.evaluation import router as evaluation_router
from hecate.ops.api.model_pricing import router as model_pricing_router
from hecate.ops.api.ops_center_overview import router as ops_center_overview_router
from hecate.ops.api.preflight import router as preflight_router
from hecate.ops.api.quotas import quotas_router
from hecate.ops.api.schedules import router as schedules_router
from hecate.ops.api.security_findings import router as security_findings_router
from hecate.ops.api.tool_analytics import router as tool_analytics_router
from hecate.ops.api.tool_decisions import router as tool_decisions_router
from hecate.runtime.api.hooks import router as hooks_router
from hecate.runtime.api.sessions import router as sessions_router
from hecate.studio.api.agent_templates import router as agent_templates_router
from hecate.studio.api.agents import router as agents_router
from hecate.studio.api.orchestration_templates import router as orchestration_templates_router
from hecate.studio.api.plugins import router as plugins_router
from hecate.studio.api.prompts import router as prompts_router
from hecate.studio.api.workflows import router as workflows_router
from hecate.tools.api.mcp import router as mcp_router
from hecate.tools.api.skill_registry import router as skill_registry_router
from hecate.tools.api.skills import router as skills_router
from hecate.tools.api.tool_cache import router as tool_cache_router
from hecate.tools.api.tool_policies import router as tool_policies_router
from hecate.tools.api.tools import router as tools_router

logger = logging.getLogger(__name__)


class _OTelAttributeMiddleware(BaseHTTPMiddleware):
    """Enriches OTel spans with request-scoped attributes."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> StarletteResponse:
        if _settings.TRACING_ENABLED:
            try:
                from opentelemetry import trace

                span = trace.get_current_span()
                if span.is_recording():
                    for header_key, attr_name in [
                        ("X-Agent-ID", "agent.id"),
                        ("X-Session-ID", "session.id"),
                        ("X-User-ID", "user.id"),
                    ]:
                        value = request.headers.get(header_key)
                        if value:
                            span.set_attribute(attr_name, value)
            except Exception:
                import logging

                logging.getLogger(__name__).debug("Failed to set OTel span attributes", exc_info=True)
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager.

    Composition lives in :mod:`hecate.core.composition.wiring`. This
    function remains as the FastAPI lifespan entry point and delegates
    the actual assembly work to ``compose_application``.
    """
    from hecate.core.composition.wiring import compose_application

    async with compose_application(app):
        yield

    # Clean up database connections (the composition root leaves the
    # engine disposal here because the engine is created in main.py's
    # module scope, not in composition).
    await engine.dispose()


app = FastAPI(
    title="Hecate Agent Platform",
    description="Enterprise-grade, self-hosted, model-agnostic, MCP-first Agent platform",
    version="0.1.0",
    lifespan=lifespan,
)


# Graceful shutdown state
SHOULD_ACCEPT_TRAFFIC: bool = True
ACTIVE_REQUESTS: int = 0
_APP_STARTUP_COMPLETE: bool = False


def _handle_sigterm(signum: int, frame: object) -> None:
    global SHOULD_ACCEPT_TRAFFIC
    SHOULD_ACCEPT_TRAFFIC = False
    logger.info("sigterm_received", extra={"signum": signum})


if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, _handle_sigterm)


async def _drain_active_requests(timeout: int = 30) -> None:
    """Wait for active requests to finish or timeout."""
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while ACTIVE_REQUESTS > 0:
        if asyncio.get_event_loop().time() >= deadline:
            logger.warning("drain_timeout", extra={"active": ACTIVE_REQUESTS, "timeout": timeout})
            break
        await asyncio.sleep(0.1)


@asynccontextmanager
async def _request_counter_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Wrap existing lifespan to track startup completion and drain in-flight requests on shutdown."""
    global _APP_STARTUP_COMPLETE
    async with lifespan(app):
        _APP_STARTUP_COMPLETE = True
        try:
            yield
        finally:
            _APP_STARTUP_COMPLETE = False
            timeout = 30
            try:
                if hasattr(_settings, "shutdown_drain_timeout"):
                    timeout = int(_settings.shutdown_drain_timeout)
            except Exception:
                pass
            await _drain_active_requests(timeout=timeout)


app.router.lifespan_context = _request_counter_lifespan


# Audit middleware — captures all HTTP requests as audit events
app.add_middleware(AuditMiddleware)


async def _request_counter_dispatch(request: Request, call_next: RequestResponseEndpoint) -> StarletteResponse:
    """Track in-flight request count for graceful shutdown drain."""
    global ACTIVE_REQUESTS
    ACTIVE_REQUESTS += 1
    try:
        return await call_next(request)
    finally:
        ACTIVE_REQUESTS -= 1


app.add_middleware(BaseHTTPMiddleware, dispatch=_request_counter_dispatch)


# OTel attribute enrichment middleware
app.add_middleware(_OTelAttributeMiddleware)
# CORS middleware - allow all origins for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler that returns unified error format.

    All API errors are returned as:
    ``{"error": {"code": "ERROR_CODE", "message": "Human-readable description", "details": null}}``

    HTTP status codes correspond to error types:
    - 400: Validation errors
    - 401: Authentication errors
    - 404: Not found errors
    - 422: Request validation errors
    - 429: Rate limit errors
    - 500: Internal server errors
    """
    import logging

    logging.getLogger(__name__).exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": None,
            }
        },
    )


async def _check_db_ready() -> bool:
    """Execute SELECT 1 via app.state session_factory, if available."""
    try:
        session_factory = getattr(app.state, "session_factory", None)
        if session_factory is None:
            return True
        async with session_factory() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("readiness_db_check_failed", exc_info=exc)
        return False


async def _check_redis_ready() -> bool:
    """PING Redis if session_state_store uses Redis."""
    try:
        session_state_store = getattr(app.state, "session_state_store", None)
        if session_state_store is None:
            return True
        redis_client = getattr(session_state_store, "_redis", None)
        if redis_client is None:
            return True
        result = redis_client.ping()
        if hasattr(result, "__await__"):
            await result
        return True
    except Exception as exc:
        logger.warning("readiness_redis_check_failed", exc_info=exc)
        return False


async def _check_qdrant_ready() -> bool:
    """Ping Qdrant if a client is configured on app.state."""
    try:
        qdrant_client = getattr(app.state, "qdrant_client", None)
        if qdrant_client is None:
            return True
        result = qdrant_client.get_collections()
        if hasattr(result, "__await__"):
            await result
        return True
    except Exception as exc:
        logger.warning("readiness_qdrant_check_failed", exc_info=exc)
        return False


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    """Liveness probe: process is alive. No external dependency checks."""
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready() -> Response:
    """Readiness probe: process can serve requests.

    Checks: SHOULD_ACCEPT_TRAFFIC flag + DB + Redis (if configured) + Qdrant (if configured).
    """
    checks: dict[str, bool] = {
        "draining": SHOULD_ACCEPT_TRAFFIC,
    }
    if not SHOULD_ACCEPT_TRAFFIC:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks, "failed": ["draining"]},
        )
    checks["database"] = await _check_db_ready()
    checks["redis"] = await _check_redis_ready()
    checks["qdrant"] = await _check_qdrant_ready()
    failed = [k for k, v in checks.items() if not v]
    if failed:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks, "failed": failed},
        )
    return JSONResponse(content={"status": "ready", "checks": checks})


@app.get("/health/startup")
async def health_startup() -> Response:
    """Startup probe: lifespan initialization complete."""
    if not _APP_STARTUP_COMPLETE:
        return JSONResponse(
            status_code=503,
            content={"status": "starting", "startup_complete": False},
        )
    return JSONResponse(content={"status": "started", "startup_complete": True})


@app.get("/version")
async def version_info() -> dict[str, str]:
    """Build info: version, commit, alembic head, python version, build date."""
    import os
    import platform

    from hecate import __version__

    git_commit = os.environ.get("GIT_COMMIT", "unknown")
    build_date = os.environ.get("BUILD_DATE", "unknown")

    alembic_head = "unknown"
    try:
        from alembic.config import Config as AlembicConfig
        from alembic.script import ScriptDirectory

        cfg = AlembicConfig("alembic.ini")
        script_dir = ScriptDirectory.from_config(cfg)
        alembic_head = script_dir.get_current_head() or "unknown"
    except Exception:
        pass

    return {
        "version": __version__,
        "commit": git_commit,
        "alembic_head": alembic_head,
        "python": platform.python_version(),
        "build_date": build_date,
    }


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint.

    Returns:
        PlainTextResponse: Metrics in Prometheus text format.
    """
    from hecate_ops.metrics import MetricsCollector

    collector = MetricsCollector()
    return PlainTextResponse(
        content=collector.export_prometheus(),
        media_type="text/plain",
    )


# /auth depends on hecate-enterprise (AuthService in hecate_enterprise.services.auth.service).
# Lazy include so core-only installs (no enterprise wheel) don't fail at import.
try:
    from hecate.enterprise.api.auth import router as auth_router

    app.include_router(auth_router, prefix="/api", tags=["auth"])
except ImportError:
    logger.debug("hecate-enterprise not installed; skipping /auth router")

app.include_router(audit_router, prefix="/api", tags=["audit"])
app.include_router(tool_decisions_router, prefix="/api", tags=["security"])
app.include_router(security_findings_router, prefix="/api", tags=["security"])
app.include_router(schedules_router, prefix="/api", tags=["schedules"])
app.include_router(evaluation_router, prefix="/api", tags=["evaluation"])
app.include_router(chat_router, prefix="/v1", tags=["chat"])
app.include_router(models_router, prefix="/v1", tags=["models"])
app.include_router(agent_chat_router, prefix="/v1", tags=["agents"])
app.include_router(agents_router, prefix="/api", tags=["agents"])
app.include_router(replay_router, prefix="/api", tags=["replay"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(tools_router, prefix="/api", tags=["tools"])
app.include_router(skills_router, prefix="/api", tags=["skills"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.include_router(workflows_router, prefix="/api", tags=["workflows"])
app.include_router(orchestration_templates_router, prefix="/api", tags=["orchestration-templates"])
app.include_router(collaboration_patterns_router, prefix="/api", tags=["collaboration-patterns"])
app.include_router(agent_templates_router, prefix="/api", tags=["agent-templates"])
app.include_router(prompts_router, prefix="/api", tags=["prompts"])
app.include_router(model_providers_router, prefix="/api", tags=["model-providers"])

# Memory + knowledge routes moved to hecate-memory in PR2.1. Lazy mount
# so core-only installs (no hecate-memory) skip silently.
try:
    from hecate_memory.api.knowledge import router as knowledge_router
    from hecate_memory.api.memory import router as memory_router

    app.include_router(knowledge_router, prefix="/api", tags=["knowledge-bases"])
    app.include_router(memory_router, prefix="/api", tags=["memory"])
except ImportError:
    logger.debug("hecate-memory not installed; skipping memory + knowledge routers")

# Monitoring routes moved to hecate-ops in PR3b. hecate-ops is a required
# dependency of core (orchestration imports hecate_ops.span_adapter at module
# level), so this import should always succeed; the guard keeps the router
# mount failure-mode identical to the other extracted-package mounts.
try:
    from hecate_ops.api.monitoring import router as monitoring_router

    app.include_router(monitoring_router, prefix="/api", tags=["monitoring"])
except ImportError:
    logger.warning("hecate-ops not installed; skipping monitoring routes")
app.include_router(api_keys_router, prefix="/api", tags=["api-keys"])
app.include_router(traces_router, prefix="/api", tags=["traces"])
app.include_router(model_pricing_router, prefix="/api", tags=["model-pricing"])
app.include_router(costs_router, prefix="/api", tags=["costs"])
app.include_router(alert_rules_router, prefix="/api", tags=["alerts"])
app.include_router(alert_events_router, prefix="/api", tags=["alerts"])
app.include_router(alert_silences_router, prefix="/api", tags=["alerts"])
app.include_router(alert_channels_router, prefix="/api", tags=["alerts"])
app.include_router(alert_escalation_policies_router, prefix="/api", tags=["alerts"])
app.include_router(quotas_router, prefix="/api", tags=["quotas"])
app.include_router(i18n_router, tags=["i18n"])
app.include_router(budget_router, tags=["budgets"])

# hecate-llm model_hub routers (cost_management, fine_tuning, inference,
# model_catalog, model_lifecycle, monitoring_models) moved to the
# packages/hecate-llm wheel in PR4b. Required dependency, but keep the
# lazy mount guard for shape consistency with memory/ops routers and so
# test/script contexts can override it cleanly.
try:
    from hecate_llm.api.management.cost_management import router as cost_management_router
    from hecate_llm.api.management.fine_tuning import router as fine_tuning_router
    from hecate_llm.api.management.inference import router as inference_router
    from hecate_llm.api.management.model_catalog import router as model_catalog_router
    from hecate_llm.api.management.model_lifecycle import router as model_lifecycle_router
    from hecate_llm.api.management.monitoring_models import router as monitoring_models_router

    app.include_router(monitoring_models_router)
    app.include_router(cost_management_router)
    app.include_router(inference_router)
    app.include_router(fine_tuning_router)
    app.include_router(model_catalog_router, tags=["model-catalog"])
    app.include_router(model_lifecycle_router, tags=["model-lifecycle"])
except ImportError:
    logger.warning("hecate-llm not installed; skipping model_hub routers")

# Enterprise-domain routers (SSO + SCIM). Lazy-imported: if
# hecate-enterprise is not installed (self-hosted without enterprise
# wheel), skip silently. ImportError is the expected downgrade signal.
try:
    from hecate_enterprise.auth.sso_routes import router as sso_router
    from hecate_enterprise.scim.discovery import router as scim_discovery_router
    from hecate_enterprise.scim.groups import router as scim_groups_router
    from hecate_enterprise.scim.users import router as scim_users_router

    app.include_router(sso_router, tags=["sso"])
    app.include_router(scim_users_router, tags=["scim"])
    app.include_router(scim_groups_router, tags=["scim"])
    app.include_router(scim_discovery_router, tags=["scim"])
except ImportError:
    logger.debug("hecate-enterprise not installed; skipping SSO/SCIM routers")

# Tenant management routes (workspaces, orgs, workspace_members). Lazy-imported
# from hecate-enterprise; if the wheel is not installed, skip silently.
try:
    from hecate_enterprise.tenant.api.orgs import router as orgs_router
    from hecate_enterprise.tenant.api.workspace_members import router as workspace_members_router
    from hecate_enterprise.tenant.api.workspaces import router as workspaces_router

    app.include_router(workspaces_router, prefix="/api", tags=["workspaces"])
    app.include_router(orgs_router, prefix="/api", tags=["orgs"])
    app.include_router(workspace_members_router, prefix="/api", tags=["workspace-members"])
except ImportError:
    logger.debug("hecate-enterprise not installed; skipping tenant management routers")

# MCP Server — conditional mount when MCP_SERVER_ENABLED=true
if _settings.MCP_SERVER_ENABLED:
    from fastmcp.utilities.lifespan import combine_lifespans

    from hecate.tools.mcp.server import create_mcp_server

    _mcp = create_mcp_server()
    # fastmcp 4 / FastAPI integration: http_app(path="/") avoids the
    # double-prefix /mcp/mcp bug (PR fastmcp#2962) when mounting at /mcp.
    _mcp_app = _mcp.http_app(path="/")
    _original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = combine_lifespans(_original_lifespan, _mcp_app.lifespan)
    app.mount("/mcp", _mcp_app)

# A2A Server — conditional mount when A2A_SERVER_ENABLED=true
if _settings.A2A_SERVER_ENABLED:
    from hecate.channel.a2a.server.app import router as a2a_router

    app.include_router(a2a_router, tags=["a2a"])

app.include_router(a2a_management_router)
app.include_router(skill_registry_router)
app.include_router(tool_analytics_router)
app.include_router(agent_health_router)
app.include_router(feature_flags_router)
app.include_router(preflight_router)
app.include_router(conversation_analytics_router)
app.include_router(ops_center_overview_router)
app.include_router(plugins_router)
app.include_router(mcp_router)
app.include_router(tool_policies_router)
app.include_router(tool_cache_router)
app.include_router(hooks_router)

# Environment routes moved to hecate-sandbox in the phase-4 follow-ups.
# Guarded mount mirrors the memory/ops/llm router pattern.
try:
    from hecate_sandbox.api.environment import router as environment_router

    app.include_router(environment_router)
except ImportError:
    logger.warning("hecate-sandbox not installed; skipping environment routes")

# Backup & Recovery API
from hecate.ops.api.backup import router as backup_router  # noqa: E402

app.include_router(backup_router, tags=["backup"])

# IM channel webhooks (Feishu + Slack) — registered after backup to keep
# the system routers grouped together. The IMMessageBus is initialized in
# the lifespan handler below.
from hecate.channel.api.v1.channels import router as im_channels_router  # noqa: E402
from hecate.channel.api.v1.im_bindings import router as im_bindings_router  # noqa: E402

app.include_router(im_channels_router)
app.include_router(im_bindings_router)

# MCP Server — conditional mount when MCP_SERVER_ENABLED=true
# (mount block is above, near the start of router registration)
