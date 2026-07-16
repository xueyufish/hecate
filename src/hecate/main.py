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
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextlib import asynccontextmanager as _asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from hecate.api.audit import router as audit_router
from hecate.api.auth import router as auth_router
from hecate.api.evaluation import router as evaluation_router
from hecate.api.management.a2a import router as a2a_management_router
from hecate.api.management.agent_health import router as agent_health_router
from hecate.api.management.agent_templates import router as agent_templates_router
from hecate.api.management.agents import router as agents_router
from hecate.api.management.alerts import (
    channels_router as alert_channels_router,
)
from hecate.api.management.alerts import (
    escalation_policies_router as alert_escalation_policies_router,
)
from hecate.api.management.alerts import (
    events_router as alert_events_router,
)
from hecate.api.management.alerts import (
    rules_router as alert_rules_router,
)
from hecate.api.management.alerts import (
    silences_router as alert_silences_router,
)
from hecate.api.management.api_keys import router as api_keys_router
from hecate.api.management.budget import router as budget_router
from hecate.api.management.collaboration_patterns import router as collaboration_patterns_router
from hecate.api.management.conversation_analytics import router as conversation_analytics_router
from hecate.api.management.conversations import router as conversations_router
from hecate.api.management.cost_management import router as cost_management_router
from hecate.api.management.costs import router as costs_router
from hecate.api.management.environment import router as environment_router
from hecate.api.management.fine_tuning import router as fine_tuning_router
from hecate.api.management.hooks import router as hooks_router
from hecate.api.management.i18n import router as i18n_router
from hecate.api.management.inference import router as inference_router
from hecate.api.management.knowledge import router as knowledge_router
from hecate.api.management.mcp import router as mcp_router
from hecate.api.management.memory import router as memory_router
from hecate.api.management.messages import router as messages_router
from hecate.api.management.model_catalog import router as model_catalog_router
from hecate.api.management.model_lifecycle import router as model_lifecycle_router
from hecate.api.management.model_pricing import router as model_pricing_router
from hecate.api.management.model_providers import router as model_providers_router
from hecate.api.management.monitoring import router as monitoring_router
from hecate.api.management.monitoring_models import router as monitoring_models_router
from hecate.api.management.ops_center_overview import router as ops_center_overview_router
from hecate.api.management.orchestration_templates import router as orchestration_templates_router
from hecate.api.management.orgs import router as orgs_router
from hecate.api.management.plugins import router as plugins_router
from hecate.api.management.prompts import router as prompts_router
from hecate.api.management.quotas import quotas_router
from hecate.api.management.sessions import router as sessions_router
from hecate.api.management.skill_registry import router as skill_registry_router
from hecate.api.management.skills import router as skills_router
from hecate.api.management.tool_analytics import router as tool_analytics_router
from hecate.api.management.tool_cache import router as tool_cache_router
from hecate.api.management.tool_policies import router as tool_policies_router
from hecate.api.management.tools import router as tools_router
from hecate.api.management.traces import router as traces_router
from hecate.api.management.workflows import router as workflows_router
from hecate.api.management.workspace_members import router as workspace_members_router
from hecate.api.management.workspaces import router as workspaces_router
from hecate.api.middleware import AuditMiddleware
from hecate.api.schedules import router as schedules_router
from hecate.api.v1.chat import router as chat_router
from hecate.api.v1.models import router as models_router
from hecate.auth.sso_routes import router as sso_router
from hecate.core.config import settings as _settings
from hecate.core.database import engine
from hecate.scim.discovery import router as scim_discovery_router
from hecate.scim.groups import router as scim_groups_router
from hecate.scim.users import router as scim_users_router

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

    Handles startup and shutdown events for the FastAPI application.
    On startup: ensures database connection pool is ready; seeds built-in tools.
    On shutdown: disposes of the database connection pool.
    """
    from hecate.core.database import async_session_factory
    from hecate.services.tool.registry import seed_builtin_tools

    async with async_session_factory() as session:
        try:
            await seed_builtin_tools(session)
            await session.commit()
        except Exception:
            await session.rollback()

    # Initialize secret providers
    from hecate.vault.registration import register_secret_providers

    register_secret_providers()

    # Discover and register plugins from the plugins directory
    try:
        from hecate.services.plugin.service import PluginService

        async with async_session_factory() as plugin_session:
            plugin_service = PluginService(plugin_session)
            summary = await plugin_service.register_discovered_plugins(_settings.PLUGINS_DIR)
            await plugin_session.commit()
            logger.info(
                "Plugin discovery: %d discovered, %d registered, %d errors",
                summary["discovered"],
                summary["registered"],
                summary["errors"],
            )
    except Exception:
        logger.exception("Plugin discovery failed")

    # Register daily budget forecast snapshot task
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        from hecate.core.database import async_session_factory

        scheduler = AsyncIOScheduler()

        async def _record_forecast_snapshots() -> None:
            async with async_session_factory() as session:
                from hecate.budget.budget_service import record_all_forecast_snapshots

                await record_all_forecast_snapshots(session)

        scheduler.add_job(
            _record_forecast_snapshots,
            trigger=CronTrigger(hour=0, minute=5),
            id="budget_forecast_snapshot",
            name="Daily Budget Forecast Snapshot",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Budget forecast scheduler started (daily at 00:05 UTC)")
    except ImportError:
        logger.info("APScheduler not available — budget forecast scheduling disabled")

    # Configure OpenTelemetry tracing
    if _settings.TRACING_ENABLED:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        # Register HecateTraceSpanProcessor for DB export
        if _settings.TRACE_DB_EXPORT_ENABLED:
            from hecate.services.observability.span_processor import HecateTraceSpanProcessor

            _trace_processor = HecateTraceSpanProcessor()
            provider.add_span_processor(_trace_processor)
            _trace_processor._ensure_consumer()

        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    # Start monitoring service
    from hecate.api.management.monitoring import get_monitoring_service

    monitoring_svc = get_monitoring_service()
    monitoring_svc.start()

    # Start audit batch writer
    import asyncio

    from hecate.api.middleware import set_audit_queue
    from hecate.services.audit.store import AuditEvent, DatabaseAuditStore
    from hecate.services.audit.writer import AuditBatchWriter

    audit_queue: asyncio.Queue[AuditEvent] = asyncio.Queue(maxsize=10000)
    set_audit_queue(audit_queue)
    audit_writer = AuditBatchWriter(DatabaseAuditStore(), audit_queue)
    await audit_writer.start()

    yield
    # Shutdown: stop audit writer
    await audit_writer.stop()
    # Shutdown: stop monitoring service
    await monitoring_svc.stop()
    # Shutdown: clean up database connections
    await engine.dispose()


app = FastAPI(
    title="Hecate Agent Platform",
    description="Enterprise-grade, self-hosted, model-agnostic, MCP-first Agent platform",
    version="0.1.0",
    lifespan=lifespan,
)

# Audit middleware — captures all HTTP requests as audit events
app.add_middleware(AuditMiddleware)

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


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint.

    Returns:
        dict: ``{"status": "ok"}`` indicating the service is running.
    """
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    """Prometheus metrics endpoint.

    Returns:
        PlainTextResponse: Metrics in Prometheus text format.
    """
    from hecate.services.observability.metrics import MetricsCollector

    collector = MetricsCollector()
    return PlainTextResponse(
        content=collector.export_prometheus(),
        media_type="text/plain",
    )


app.include_router(auth_router, prefix="/api", tags=["auth"])
app.include_router(audit_router, prefix="/api", tags=["audit"])
app.include_router(schedules_router, prefix="/api", tags=["schedules"])
app.include_router(evaluation_router, prefix="/api", tags=["evaluation"])
app.include_router(chat_router, prefix="/v1", tags=["chat"])
app.include_router(models_router, prefix="/v1", tags=["models"])
app.include_router(agents_router, prefix="/api", tags=["agents"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(tools_router, prefix="/api", tags=["tools"])
app.include_router(skills_router, prefix="/api", tags=["skills"])
app.include_router(knowledge_router, prefix="/api", tags=["knowledge-bases"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.include_router(messages_router, prefix="/api", tags=["messages"])
app.include_router(workflows_router, prefix="/api", tags=["workflows"])
app.include_router(orchestration_templates_router, prefix="/api", tags=["orchestration-templates"])
app.include_router(collaboration_patterns_router, prefix="/api", tags=["collaboration-patterns"])
app.include_router(agent_templates_router, prefix="/api", tags=["agent-templates"])
app.include_router(memory_router, prefix="/api", tags=["memory"])
app.include_router(prompts_router, prefix="/api", tags=["prompts"])
app.include_router(model_providers_router, prefix="/api", tags=["model-providers"])
app.include_router(orgs_router, prefix="/api", tags=["orgs"])
app.include_router(workspaces_router, prefix="/api", tags=["workspaces"])
app.include_router(workspace_members_router, prefix="/api", tags=["workspace-members"])
app.include_router(api_keys_router, prefix="/api", tags=["api-keys"])
app.include_router(traces_router, prefix="/api", tags=["traces"])
app.include_router(monitoring_router, prefix="/api", tags=["monitoring"])
app.include_router(monitoring_models_router)
app.include_router(model_pricing_router, prefix="/api", tags=["model-pricing"])
app.include_router(costs_router, prefix="/api", tags=["costs"])
app.include_router(cost_management_router)
app.include_router(alert_rules_router, prefix="/api", tags=["alerts"])
app.include_router(alert_events_router, prefix="/api", tags=["alerts"])
app.include_router(alert_silences_router, prefix="/api", tags=["alerts"])
app.include_router(alert_channels_router, prefix="/api", tags=["alerts"])
app.include_router(alert_escalation_policies_router, prefix="/api", tags=["alerts"])
app.include_router(quotas_router, prefix="/api", tags=["quotas"])
app.include_router(i18n_router, tags=["i18n"])
app.include_router(inference_router)
app.include_router(fine_tuning_router)
app.include_router(budget_router, tags=["budgets"])
app.include_router(model_catalog_router, tags=["model-catalog"])
app.include_router(model_lifecycle_router, tags=["model-lifecycle"])
app.include_router(sso_router, tags=["sso"])
app.include_router(scim_users_router, tags=["scim"])
app.include_router(scim_groups_router, tags=["scim"])
app.include_router(scim_discovery_router, tags=["scim"])

# MCP Server — conditional mount when MCP_SERVER_ENABLED=true
if _settings.MCP_SERVER_ENABLED:
    from hecate.services.mcp.server import create_mcp_server

    _mcp = create_mcp_server()
    _mcp_app = _mcp.http_app(path="/mcp")

    _original_lifespan = app.router.lifespan_context

    @_asynccontextmanager
    async def _combined_lifespan(app_inner: FastAPI) -> AsyncGenerator[None, None]:
        async with (
            _original_lifespan(app_inner),
            _mcp_app.lifespan(app_inner),
        ):
            yield

    app.router.lifespan_context = _combined_lifespan

# A2A Server — conditional mount when A2A_SERVER_ENABLED=true
if _settings.A2A_SERVER_ENABLED:
    from hecate.a2a.server.app import router as a2a_router

    app.include_router(a2a_router, tags=["a2a"])

app.include_router(a2a_management_router)
app.include_router(skill_registry_router)
app.include_router(tool_analytics_router)
app.include_router(agent_health_router)
app.include_router(conversation_analytics_router)
app.include_router(ops_center_overview_router)
app.include_router(plugins_router)
app.include_router(mcp_router)
app.include_router(tool_policies_router)
app.include_router(tool_cache_router)
app.include_router(hooks_router)
app.include_router(environment_router)

# MCP Server — conditional mount when MCP_SERVER_ENABLED=true
if _settings.MCP_SERVER_ENABLED:
    from hecate.services.mcp.server import create_mcp_server

    _mcp = create_mcp_server()
    _mcp_app = _mcp.http_app(path="/mcp")

    _original_lifespan = app.router.lifespan_context

    @_asynccontextmanager
    async def _combined_lifespan(app_inner: FastAPI) -> AsyncGenerator[None, None]:
        async with (
            _original_lifespan(app_inner),
            _mcp_app.lifespan(app_inner),
        ):
            yield

    app.router.lifespan_context = _combined_lifespan
