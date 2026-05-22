"""Async SQLAlchemy engine, session factory, and declarative base.

Creates the async database engine and session maker from
:class:`hecate.core.config.Settings`, and provides the ``Base`` declarative
base class that every ORM model inherits from.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from hecate.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=10,
)

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
