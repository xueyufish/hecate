"""Application composition — the wiring layer of FastAPI startup.

This module owns every piece of state that the FastAPI app needs
before it can serve a single request. Each helper is a small
function that does one thing; the ``compose_application`` function
calls them in dependency order.

The split between this module and ``main.py`` is:

- ``wiring.py`` owns **what** gets initialised (the dependencies,
  the singletons, the lifecycle hooks).
- ``main.py`` owns **how** the FastAPI app is configured (routes,
  middleware, exception handlers) and the lifespan itself; it
  delegates the assembly work to ``wiring.py``.

History
-------

Phase R-MVP and the six Phase R-complete domain moves (PR-A through
PR-C, PR-D.1) left ``main.py``'s lifespan with 11 inline assembly
blocks totalling ~150 lines. PR-E.1 pulls those blocks out into
this module — the FastAPI app's lifespan shrinks to a single call,
and future assembly changes (e.g. a new workspace wheel adding a
singleton) live next to the rest of the composition glue instead
of in main.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual assembly blocks. Each function does one piece of setup and
# (optionally) records state on ``app.state`` or returns handles the lifespan
# shutdown path needs.
# ---------------------------------------------------------------------------


async def seed_builtin_tools() -> None:
    """Seed the registry with the built-in tool definitions."""
    from hecate.core.database import async_session_factory
    from hecate.tools.tool.registry import seed_builtin_tools as _seed

    async with async_session_factory() as session:
        try:
            await _seed(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Built-in tool seed failed; continuing")


def register_secret_providers() -> None:
    """Register hecate-enterprise vault backend implementations.

    Lazy-imported so that self-hosted installs (no hecate-enterprise
    wheel) still boot cleanly. The historical ``register_secret_providers``
    function in hecate-enterprise does the actual entry-point wiring.
    """
    try:
        from hecate_enterprise.vault.registration import (
            register_secret_providers as _register,
        )

        _register()
    except ImportError:
        logger.debug("hecate-enterprise not installed; skipping secret-provider registration")


def attach_state_stores(app: FastAPI) -> None:
    """Construct process-wide EventStore and SessionStateStore singletons."""
    from hecate.core.config import settings
    from hecate.studio.event_state import create_event_store
    from hecate.studio.session_state import create_session_state_store

    app.state.event_store = create_event_store(settings)
    app.state.session_state_store = create_session_state_store(settings)
    logger.info("EventStore backend=%s", settings.EVENT_STORE_BACKEND)
    logger.info("SessionStateStore backend=%s", settings.SESSION_STATE_STORE_BACKEND)


def attach_dlp_scanner(app: FastAPI) -> None:
    """Build the base DLP scanner and attach it to ``app.state``.

    The base scanner is the fast in-memory fallback for callers that
    don't need DB-backed config; per-request DB-backed rules are
    layered on by DLPService at request time.
    """
    from hecate.core.config import settings
    from hecate.ops.dlp.policy import DLPPolicyResolver
    from hecate.ops.dlp.recognizer import DLPRecognizerRegistry
    from hecate.ops.dlp.recognizers.regex import RegexRecognizer
    from hecate.ops.dlp.scanner import DLPScanner

    if not settings.DLP_ENABLED:
        app.state.dlp_scanner = None
        logger.info("DLP engine disabled (DLP_ENABLED=false)")
        return

    base_registry = DLPRecognizerRegistry()
    base_registry.register(RegexRecognizer())
    app.state.dlp_scanner = DLPScanner(base_registry, policy=DLPPolicyResolver(rules=[]))
    logger.info(
        "DLP engine initialized (buffer=%d overlap=%d)",
        settings.DLP_STREAM_BUFFER_SIZE,
        settings.DLP_STREAM_OVERLAP,
    )


async def discover_plugins() -> None:
    """Discover and register plugins from the plugins directory."""
    from hecate.core.config import settings
    from hecate.core.database import async_session_factory
    from hecate.studio.plugin.service import PluginService

    try:
        async with async_session_factory() as session:
            plugin_service = PluginService(session)
            summary = await plugin_service.register_discovered_plugins(settings.PLUGINS_DIR)
            await session.commit()
            logger.info(
                "Plugin discovery: %d discovered, %d registered, %d errors",
                summary["discovered"],
                summary["registered"],
                summary["errors"],
            )
    except Exception:
        logger.exception("Plugin discovery failed")


async def replay_agent_plugin_mcp() -> None:
    """Replay MCP registrations for enabled agent plugins; remove orphan dirs."""
    from hecate.core.config import settings
    from hecate.core.database import async_session_factory
    from hecate.studio.plugin.service import PluginService

    try:
        async with async_session_factory() as session:
            plugin_service = PluginService(session)
            replayed = await plugin_service.replay_agent_plugin_mcp()
            orphans = await plugin_service.cleanup_orphan_agent_plugin_dirs(settings.PLUGINS_DIR)
            await session.commit()
            if replayed or orphans:
                logger.info(
                    "Agent Plugins maintenance: %d packages replayed, %d orphan dirs removed",
                    replayed,
                    orphans,
                )
    except Exception:
        logger.exception("Agent Plugins maintenance failed")


async def start_meta_agents(app: FastAPI) -> None:
    """Start the meta-agent scheduler (compliance + garbage collector).

    DriftDetector is intentionally not registered — it needs an
    expected-baseline source that deployment tooling must define
    first.
    """
    from hecate.core.config import settings
    from hecate.core.database import async_session_factory
    from hecate.studio.meta_agents.compliance_checker import ComplianceCheckerAgent
    from hecate.studio.meta_agents.garbage_collector import GarbageCollectorAgent
    from hecate.studio.meta_agents.scheduler import MetaAgentScheduler

    if not settings.META_AGENTS_ENABLED:
        app.state.meta_scheduler = None
        return

    try:
        interval = settings.META_AGENTS_INTERVAL_SECONDS
        gc_agent = GarbageCollectorAgent()
        compliance_agent = ComplianceCheckerAgent()

        async def _gc_tick() -> None:
            async with async_session_factory() as session:
                await gc_agent.run(session)

        scheduler = MetaAgentScheduler()
        scheduler.register("garbage_collector", _gc_tick, interval_seconds=interval)
        scheduler.register("compliance_checker", compliance_agent.run, interval_seconds=interval)
        await scheduler.start()
        app.state.meta_scheduler = scheduler
        logger.info(
            "Meta-agents started (interval=%ds): garbage_collector, compliance_checker",
            interval,
        )
    except Exception:
        logger.exception("Meta-agent startup failed; continuing without meta-agents")
        app.state.meta_scheduler = None


async def register_im_channels(app: FastAPI) -> None:
    """Register IM channel adapters (Feishu, Slack) and start the message bus."""
    from hecate.channel.gateway.registration import register_channels, register_im_channels
    from hecate.channel.im.message_bus import IMMessageBus
    from hecate.core.plugin.registry import PluginRegistry

    try:
        plugin_registry = PluginRegistry()
        register_channels(plugin_registry)
        registered_im = register_im_channels(plugin_registry)
        app.state.plugin_registry = plugin_registry

        im_bus = IMMessageBus()
        await im_bus.start()
        app.state.im_message_bus = im_bus
        logger.info("IM channels initialized: %d IM adapter(s) registered", registered_im)
    except Exception:
        logger.exception("IM channel initialization failed; continuing without IM support")


def start_budget_scheduler() -> None:
    """Schedule daily budget forecast snapshots."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        from hecate.core.database import async_session_factory

        scheduler = AsyncIOScheduler()

        async def _record_forecast_snapshots() -> None:
            async with async_session_factory() as session:
                from hecate_enterprise.budget.budget_service import (
                    record_all_forecast_snapshots,
                )

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


def configure_tracing(app: FastAPI) -> None:
    """Configure OpenTelemetry tracing (OTLP exporter when configured).

    Stores the provider on ``app.state`` so the shutdown phase can
    stop its background threads (otherwise the global tracer keeps
    recording into HecateTraceSpanProcessor's queue, which drains
    toward the production database — hangs test processes).
    """
    app.state.tracing_provider = None
    try:
        from hecate_ops.otel_setup import configure_tracing as _configure

        app.state.tracing_provider = _configure(app)
    except ImportError:
        logger.warning("observability extras not installed — tracing disabled")


def start_monitoring() -> None:
    """Start the OTel-backed monitoring service (WebSocket push)."""
    from hecate_ops.api.monitoring import get_monitoring_service

    monitoring_svc = get_monitoring_service()
    monitoring_svc.start()


async def start_audit_batch_writer() -> None:
    """Start the audit log batch writer (DB bridge)."""
    from hecate.api.middleware import set_audit_queue
    from hecate.ops.audit.store import AuditEvent, DatabaseAuditStore
    from hecate.ops.audit.writer import AuditBatchWriter

    audit_queue: asyncio.Queue[AuditEvent] = asyncio.Queue(maxsize=10000)
    set_audit_queue(audit_queue)
    audit_writer = AuditBatchWriter(DatabaseAuditStore(), audit_queue)
    await audit_writer.start()


async def prewarm_sandbox_pool() -> None:
    """Prewarm the sandbox container pool on startup."""
    from hecate.core.config import settings

    if not settings.SANDBOX_POOL_ENABLED:
        return
    from hecate_sandbox.sandbox import get_sandbox_pool

    sandbox_pool = get_sandbox_pool()
    if sandbox_pool:
        await sandbox_pool.prewarm()
        logger.info("Sandbox container pool prewarmed: %d containers", sandbox_pool.total_count)


def start_tool_decision_pipeline() -> None:
    """Wire the structured tool-decision event pipeline."""
    from hecate.api.tool_decisions import set_tool_decision_service
    from hecate.core.config import settings
    from hecate.ops.tool_decisions import ToolDecisionService
    from hecate.runtime.decision_sink import decision_emitter

    if not settings.AGENT_ENV_DECISION_ENABLED:
        return
    tool_decision_svc = ToolDecisionService()
    # No await here — ToolDecisionService.start is not async; if
    # future implementations add a startup hook, this is the seam.
    decision_emitter.set_sink(tool_decision_svc)
    set_tool_decision_service(tool_decision_svc)


def start_security_findings() -> None:
    """Attach the security finding persistence service."""
    from hecate.api.security_findings import set_security_finding_service
    from hecate.ops.security.findings import SecurityFindingService

    set_security_finding_service(SecurityFindingService())


async def start_siem_export(app: FastAPI) -> None:
    """Start the SIEM export pipeline when SIEM_ENABLED is set."""
    from hecate.core.config import settings
    from hecate.ops.siem.collector import (
        SecurityEventCollector,
        set_collector,
    )

    if not settings.SIEM_ENABLED:
        app.state.siem_collector = None
        return
    siem_collector = SecurityEventCollector()
    set_collector(siem_collector)
    await siem_collector.start()
    app.state.siem_collector = siem_collector
    logger.info("SIEM export pipeline started")


# ---------------------------------------------------------------------------
# Composition — the single FastAPI lifespan entry point.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def compose_application(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: build the composition and tear it down on exit.

    Replaces the 150-line lifespan handler that used to live inline in
    main.py. The shutdown path mirrors startup in reverse — anything
    with a start that needs cleanup is cleaned up here in reverse order.
    """
    # Startup (dependency order: cheap singletons first, then state stores,
    # then scanners / pipelines, then scheduled tasks).
    await seed_builtin_tools()
    register_secret_providers()
    attach_state_stores(app)
    attach_dlp_scanner(app)
    await discover_plugins()
    await replay_agent_plugin_mcp()
    await start_meta_agents(app)
    await register_im_channels(app)
    start_budget_scheduler()
    configure_tracing(app)
    start_monitoring()
    await start_audit_batch_writer()
    await prewarm_sandbox_pool()
    start_tool_decision_pipeline()
    start_security_findings()
    await start_siem_export(app)

    try:
        yield
    finally:
        # Shutdown — reverse order.
        siem_collector = getattr(app.state, "siem_collector", None)
        if siem_collector is not None:
            try:
                await siem_collector.stop()
            except Exception:
                logger.exception("SIEM collector shutdown failed")
        tracing_provider = getattr(app.state, "tracing_provider", None)
        if tracing_provider is not None:
            try:
                tracing_provider.shutdown()
            except Exception:
                logger.exception("Tracing provider shutdown failed")
        meta_scheduler = getattr(app.state, "meta_scheduler", None)
        if meta_scheduler is not None:
            try:
                await meta_scheduler.stop()
            except Exception:
                logger.exception("Meta-agent scheduler shutdown failed")
