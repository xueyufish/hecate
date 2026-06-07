"""Async SQLAlchemy engine, session factory, and declarative base.

Creates the async database engine and session maker from
:class:`hecate.core.config.Settings`, and provides the ``Base`` declarative
base class that every ORM model inherits from.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from hecate.core.config import settings

SUPPORTED_DIALECTS = ("postgresql", "mysql", "sqlite")


def create_engine_from_url(url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine with dialect-appropriate pool settings.

    Supported dialects:

    - **postgresql** — connection pooling (pool_size=20, max_overflow=10)
    - **mysql** — connection pooling (pool_size=20, max_overflow=10)
    - **sqlite** — no pooling for file-based; ``StaticPool`` for in-memory

    Raises:
        ValueError: If the URL scheme is not a supported dialect.
        ImportError: If MySQL is selected but ``aiomysql`` is not installed.
    """
    dialect = url.split("://")[0].split("+")[0]

    if dialect not in SUPPORTED_DIALECTS:
        raise ValueError(f"Unsupported database dialect: {dialect}. Supported: {', '.join(SUPPORTED_DIALECTS)}")

    if dialect == "mysql":
        try:
            import aiomysql  # noqa: F401
        except ImportError:
            raise ImportError("MySQL support requires aiomysql. Install with: pip install hecate[mysql]") from None
        return create_async_engine(url, echo=False, pool_size=20, max_overflow=10)

    if dialect == "postgresql":
        return create_async_engine(url, echo=False, pool_size=20, max_overflow=10)

    # sqlite
    if "://" in url and url.split("://")[1] in ("", "/"):
        # In-memory SQLite — use StaticPool to maintain single connection
        return create_async_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    # File-based SQLite — default pool
    return create_async_engine(url, echo=False)


engine = create_engine_from_url(settings.DATABASE_URL)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base for all Hecate ORM models.

    Every model class (agents, sessions, messages, etc.) inherits from
    :class:`Base` so that SQLAlchemy can track table metadata and
    relationships in a single registry.
    """


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    On successful handler completion the session is automatically committed.
    If an exception escapes the handler the session is rolled back and the
    exception is re-raised, ensuring the database is never left in a partial
    state.

    Usage::

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise exc
