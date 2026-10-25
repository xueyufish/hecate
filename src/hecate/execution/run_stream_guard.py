"""Run SSE stream access guard (execution domain).

An established ``GET /api/runs/{run_id}/events/stream`` connection must
keep honoring the caller's current identity, the VIEWER floor, and the
Run/parent-Task visibility — not the facts frozen at connection time.
This module re-derives those facts on demand:

- the original Bearer credential is replayed through the shared
  ``authenticate_bearer`` chain, so JWT expiry, deactivated users,
  removed memberships, role downgrades, and revoked or disabled API
  keys all surface as they would on a fresh request;
- the re-authenticated context must retain the user, organization,
  authentication method, scope, and original workspace — it never
  re-targets the stream at a different tenant or authority;
- the run and its parent task must still be live rows in that workspace
  (missing, foreign, and soft-deleted stay indistinguishable).

Every fact read runs on a short-lived session from the process session
factory (resolved at call time so tests and embedded deployments can
point it at their own database); the frozen ``AuthContext`` and any
long-lived session's identity map are never treated as current
authorization facts. The session closes without committing, so provider
side effects like API-key ``last_used_at``
touch-ups are discarded rather than persisted by an observation stream.

Failures map to the SSE terminal status the route reports: 401 for
invalid or no-longer-resolvable credentials, 403 for insufficient
workspace access, 404 for an invisible run/task pair, and 500 when the
check itself cannot run (the stream then refuses to read or relay more
content rather than continuing on unverified facts).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.core.auth_context import AuthContext
from hecate.core.deps_workspace import (
    WorkspaceRole,
    WorkspaceRoleRequiredError,
    authenticate_bearer,
    ensure_workspace_role,
)

logger = logging.getLogger(__name__)

#: How long an established stream may run on one access verification.
#: Reads and relays are only issued while the last verification is at
#: most this old, and idle streams re-verify on the same cadence. No
#: further envelope is sent while a due check is pending. An in-flight
#: read or network send cannot be undone; the next operation rechecks.
RECHECK_INTERVAL_SECONDS = 5.0

#: An unresponsive verifier must not keep an idle observation open forever.
RECHECK_TIMEOUT_SECONDS = 5.0


class RunStreamAccessError(Exception):
    """The stream's access no longer holds; carries the terminal SSE status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"run stream access ended with HTTP {status_code}")
        self.status_code = status_code


class PeriodicAccessGuard:
    """Due-based wrapper driving one recheck from inside a stream loop.

    The stream loop calls :meth:`before_read` ahead of each event read (and
    its relay projection), :meth:`before_send` ahead of relaying a batch it
    just obtained and each envelope, and :meth:`idle` in place of its poll
    sleep. Checks only run when the last verification has aged past ``interval`` — so a hot
    stream costs at most one verification per interval — while idle streams
    still verify on the same cadence. Any failure raises
    :class:`RunStreamAccessError` through the stream loop, which maps to
    the terminal code-only SSE error frame.
    """

    def __init__(self, recheck: Callable[[], Awaitable[None]], interval: float) -> None:
        self._recheck = recheck
        self._interval = interval
        self._last_check = time.monotonic()

    async def _run_if_due(self) -> None:
        started = time.monotonic()
        if started - self._last_check < self._interval:
            return
        await self._recheck()
        # The facts may have been read near the start of a slow check.
        self._last_check = started

    async def before_read(self) -> None:
        """Verify before the next event read or relay projection."""
        await self._run_if_due()

    async def before_send(self) -> None:
        """Verify before relaying a batch the loop already obtained."""
        await self._run_if_due()

    async def idle(self, poll_interval: float) -> None:
        """Sleep the poll interval, waking to verify at least per interval."""
        deadline = time.monotonic() + poll_interval
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            until_check = self._last_check + self._interval - time.monotonic()
            await asyncio.sleep(max(0.0, min(remaining, until_check)))
            await self._run_if_due()


def _session_factory() -> async_sessionmaker[AsyncSession]:
    # Resolved per call so deployments and tests can substitute their own
    # database binding without touching this module.
    from hecate.core.database import async_session_factory

    return async_session_factory


async def recheck_stream_access(
    *,
    token: str | None,
    bound_ctx: AuthContext,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Re-verify an established stream's access facts on a fresh session.

    Raises :class:`RunStreamAccessError` carrying the terminal SSE status
    (401/403/404) when any fact no longer holds, or 500 when the check
    itself fails — the caller must then stop reading and relaying.
    """
    try:
        async with asyncio.timeout(RECHECK_TIMEOUT_SECONDS):
            factory = session_factory if session_factory is not None else _session_factory()
            async with factory() as session:
                await _verify_on(
                    session,
                    token=token,
                    bound_ctx=bound_ctx,
                    workspace_id=workspace_id,
                    run_id=run_id,
                )
    except RunStreamAccessError:
        raise
    except Exception as exc:  # noqa: BLE001 — any infra failure ends the stream
        # Never publish the exception text; it may carry SQL, URLs, or payloads.
        logger.error("Run stream recheck failed for run %s (%s)", run_id, type(exc).__name__)
        raise RunStreamAccessError(500) from exc


async def _verify_on(
    session: AsyncSession,
    *,
    token: str | None,
    bound_ctx: AuthContext,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> None:
    from hecate.execution.task_run_registry import TaskNotFoundError, TaskRunRegistry

    current = await authenticate_bearer(token, session, raise_provider_errors=True) if token else None
    if current is None or (current.user_id, current.org_id, current.auth_method, current.api_key_scope) != (
        bound_ctx.user_id,
        bound_ctx.org_id,
        bound_ctx.auth_method,
        bound_ctx.api_key_scope,
    ):
        # Changing identity or authority must not silently upgrade an
        # already established subscription.
        raise RunStreamAccessError(401)
    if bound_ctx.is_system_scope:
        if not current.is_system_scope:
            raise RunStreamAccessError(401)
    else:
        if current.workspace_id != workspace_id:
            # Re-authentication must not re-target the stream's tenant.
            raise RunStreamAccessError(403)
        try:
            ensure_workspace_role(current, WorkspaceRole.VIEWER)
        except WorkspaceRoleRequiredError as exc:
            raise RunStreamAccessError(403) from exc

    registry = TaskRunRegistry(session)
    try:
        run = await registry.get_run(run_id, workspace_id)
        await registry.get_task(run.task_id, workspace_id)
    except TaskNotFoundError:
        raise RunStreamAccessError(404) from None


__all__ = [
    "RECHECK_INTERVAL_SECONDS",
    "PeriodicAccessGuard",
    "RunStreamAccessError",
    "recheck_stream_access",
]
