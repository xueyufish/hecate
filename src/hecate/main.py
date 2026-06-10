"""FastAPI application entry point for Hecate Agent Platform.

Initializes the FastAPI application with:
- CORS middleware (allows all origins for development)
- Unified error handling with consistent JSON error format
- Lifespan events for database connection management
- Health check endpoint at ``GET /health``
- Route registration for ``/v1`` (OpenAI compatible) and ``/api`` (management)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextlib import asynccontextmanager as _asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from hecate.api.auth import router as auth_router
from hecate.api.evaluation import router as evaluation_router
from hecate.api.management.agent_templates import router as agent_templates_router
from hecate.api.management.agents import router as agents_router
from hecate.api.management.api_keys import router as api_keys_router
from hecate.api.management.conversations import router as conversations_router
from hecate.api.management.knowledge import router as knowledge_router
from hecate.api.management.memory import router as memory_router
from hecate.api.management.messages import router as messages_router
from hecate.api.management.model_providers import router as model_providers_router
from hecate.api.management.monitoring import router as monitoring_router
from hecate.api.management.orchestration_templates import router as orchestration_templates_router
from hecate.api.management.orgs import router as orgs_router
from hecate.api.management.prompts import router as prompts_router
from hecate.api.management.sessions import router as sessions_router
from hecate.api.management.skills import router as skills_router
from hecate.api.management.tools import router as tools_router
from hecate.api.management.traces import router as traces_router
from hecate.api.management.workflows import router as workflows_router
from hecate.api.management.workspace_members import router as workspace_members_router
from hecate.api.management.workspaces import router as workspaces_router
from hecate.api.v1.chat import router as chat_router
from hecate.api.v1.models import router as models_router
from hecate.core.config import settings as _settings
from hecate.core.database import engine


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

    # Configure OpenTelemetry tracing
    if _settings.TRACING_ENABLED:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)

    # Start monitoring service
    from hecate.api.management.monitoring import get_monitoring_service

    monitoring_svc = get_monitoring_service()
    monitoring_svc.start()

    yield
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
    app.mount("/mcp", _mcp_app)
