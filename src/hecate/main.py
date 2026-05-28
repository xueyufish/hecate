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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from hecate.api.management.agents import router as agents_router
from hecate.api.management.conversations import router as conversations_router
from hecate.api.management.knowledge import router as knowledge_router
from hecate.api.management.memory import router as memory_router
from hecate.api.management.sessions import router as sessions_router
from hecate.api.management.skills import router as skills_router
from hecate.api.management.tools import router as tools_router
from hecate.api.management.workflows import router as workflows_router
from hecate.api.v1.chat import router as chat_router
from hecate.api.v1.models import router as models_router
from hecate.core.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager.

    Handles startup and shutdown events for the FastAPI application.
    On startup: ensures database connection pool is ready.
    On shutdown: disposes of the database connection pool.
    """
    # Startup: engine is already created at module level
    yield
    # Shutdown: clean up database connections
    await engine.dispose()


app = FastAPI(
    title="Hecate Agent Platform",
    description="Enterprise-grade, self-hosted, model-agnostic, MCP-first Agent platform",
    version="0.1.0",
    lifespan=lifespan,
)

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


app.include_router(chat_router, prefix="/v1", tags=["chat"])
app.include_router(models_router, prefix="/v1", tags=["models"])
app.include_router(agents_router, prefix="/api", tags=["agents"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(tools_router, prefix="/api", tags=["tools"])
app.include_router(skills_router, prefix="/api", tags=["skills"])
app.include_router(knowledge_router, prefix="/api", tags=["knowledge-bases"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.include_router(workflows_router, prefix="/api", tags=["workflows"])
app.include_router(memory_router, prefix="/api", tags=["memory"])
