"""Resource-key leases with monotonic fencing tokens.

A lease names a resource key (a run, a host slot, any serialized unit), a
holder, and an expiry. Every ownership change bumps the row's fencing token:
an expired holder that lost the lease keeps writing with its old token and is
rejected (:class:`StaleFenceError`) — the classic fencing guarantee, applied
at storage level so "the old owner keeps writing after lease expiry" fails
closed instead of silently overwriting the new owner.

Expiry comparisons use an injectable clock (epoch seconds) so tests advance
time deterministically. SQLite has no ``SELECT .. FOR UPDATE`` (SQLAlchemy
omits it) but serializes writers, so the read-decide-write sequence below is
atomic on both dialects.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hecate_durable.storage.models import LeaseRow


class StaleFenceError(Exception):
    """A write carried a fencing token older than the resource's current one."""

    def __init__(self, lease_key: str, presented_token: int, current_token: int) -> None:
        super().__init__(
            f"lease {lease_key!r}: presented fencing token {presented_token} is stale "
            f"(current is {current_token}); the write is rejected"
        )
        self.lease_key = lease_key
        self.presented_token = presented_token
        self.current_token = current_token


@dataclass(frozen=True)
class LeaseHandle:
    """Issued lease: holder identity plus the fencing token to present."""

    lease_key: str
    holder: str
    fencing_token: int
    expires_at: str

    def assert_current(self, current_token: int) -> None:
        if self.fencing_token != current_token:
            raise StaleFenceError(self.lease_key, self.fencing_token, current_token)


def _default_clock() -> datetime:
    return datetime.now(UTC)


class LeaseManager:
    """Acquire/renew/release leases; validate fencing tokens."""

    def __init__(self, session_factory: Callable[[], Session], *, clock: Callable[[], datetime] | None = None) -> None:
        self._session_factory = session_factory
        self._clock = clock or _default_clock

    def acquire(self, lease_key: str, holder: str, ttl_seconds: float) -> LeaseHandle | None:
        """Take the lease when free, expired, or already held by ``holder``.

        Returns ``None`` when another holder's lease is still valid. Taking
        over any expired or released lease bumps the fencing token, including
        a restart using the same holder name. Only a still-live re-acquisition
        by the same holder extends it without changing generation.
        """

        now = self._clock()
        for _ in range(3):
            with self._session_factory() as session, session.begin():
                row = session.execute(
                    select(LeaseRow).where(LeaseRow.lease_key == lease_key).with_for_update()
                ).scalar_one_or_none()
                expires_at = _iso(now.timestamp() + ttl_seconds)
                if row is None:
                    try:
                        with session.begin_nested():
                            session.add(
                                LeaseRow(
                                    lease_key=lease_key,
                                    holder=holder,
                                    fencing_token=1,
                                    expires_at=expires_at,
                                    expires_at_epoch=now.timestamp() + ttl_seconds,
                                    acquired_at=now.isoformat(),
                                )
                            )
                        return LeaseHandle(lease_key, holder, 1, expires_at)
                    except IntegrityError:
                        continue  # concurrent creator won; retry the decision path
                if row.holder == holder and row.expires_at_epoch > now.timestamp():
                    session.execute(
                        update(LeaseRow)
                        .where(LeaseRow.lease_key == lease_key)
                        .values(expires_at=expires_at, expires_at_epoch=now.timestamp() + ttl_seconds)
                    )
                    return LeaseHandle(lease_key, holder, row.fencing_token, expires_at)
                if row.expires_at_epoch <= now.timestamp():
                    token = row.fencing_token + 1
                    session.execute(
                        update(LeaseRow)
                        .where(LeaseRow.lease_key == lease_key)
                        .values(
                            holder=holder,
                            fencing_token=token,
                            expires_at=expires_at,
                            expires_at_epoch=now.timestamp() + ttl_seconds,
                            acquired_at=now.isoformat(),
                        )
                    )
                    return LeaseHandle(lease_key, holder, token, expires_at)
                return None
        raise RuntimeError("lease acquire did not converge")  # pragma: no cover

    def renew(
        self, lease_key: str, holder: str, ttl_seconds: float, *, fencing_token: int | None = None
    ) -> LeaseHandle | None:
        """Extend a lease still held by ``holder``; ``None`` when it was lost."""

        now = self._clock()
        with self._session_factory() as session, session.begin():
            row = session.execute(
                select(LeaseRow).where(LeaseRow.lease_key == lease_key).with_for_update()
            ).scalar_one_or_none()
            if (
                row is None
                or row.holder != holder
                or row.expires_at_epoch <= now.timestamp()
                or (fencing_token is not None and row.fencing_token != fencing_token)
            ):
                return None
            expires_at = _iso(now.timestamp() + ttl_seconds)
            session.execute(
                update(LeaseRow)
                .where(LeaseRow.lease_key == lease_key)
                .values(expires_at=expires_at, expires_at_epoch=now.timestamp() + ttl_seconds)
            )
            return LeaseHandle(lease_key, holder, row.fencing_token, expires_at)

    def release(self, lease_key: str, holder: str, *, fencing_token: int | None = None) -> bool:
        """Release when still held by ``holder``; the fencing token stays."""

        with self._session_factory() as session, session.begin():
            statement = update(LeaseRow).where(LeaseRow.lease_key == lease_key, LeaseRow.holder == holder)
            if fencing_token is not None:
                statement = statement.where(LeaseRow.fencing_token == fencing_token)
            result = session.execute(statement.values(expires_at=_iso(0.0), expires_at_epoch=0.0))
            return result.rowcount == 1

    def assert_valid(self, session: Session, handle: LeaseHandle) -> None:
        """Fence a state write inside its transaction, including lease expiry."""
        row = session.execute(
            select(LeaseRow).where(LeaseRow.lease_key == handle.lease_key).with_for_update()
        ).scalar_one_or_none()
        if (
            row is None
            or row.holder != handle.holder
            or row.fencing_token != handle.fencing_token
            or row.expires_at_epoch <= self._clock().timestamp()
        ):
            raise StaleFenceError(handle.lease_key, handle.fencing_token, row.fencing_token if row else -1)

    def current_token(self, lease_key: str) -> int | None:
        """Current fencing token for the lease, or ``None`` when never issued."""

        with self._session_factory() as session:
            row = session.execute(select(LeaseRow).where(LeaseRow.lease_key == lease_key)).scalar_one_or_none()
            return None if row is None else row.fencing_token

    def validate_fence(self, lease_key: str, presented_token: int) -> None:
        """Raise :class:`StaleFenceError` unless the token is current."""

        current = self.current_token(lease_key)
        if current is None or current != presented_token:
            raise StaleFenceError(lease_key, presented_token, current if current is not None else -1)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
