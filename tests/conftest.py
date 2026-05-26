"""Shared pytest fixtures for the test suite.

Provides a self-contained test infrastructure that keeps each test isolated:

- **In-memory SQLite** via ``sqlite+aiosqlite://`` so no external database is
  required and tests run fast with zero side-effects.
- **Session-scoped event loop** so ``pytest-asyncio`` shares one loop across
  all async tests in a session, matching the library's recommended setup.
- **Per-test database lifecycle** — ``setup_database`` creates all tables
  before each test and drops them afterwards, guaranteeing a clean schema.
- **Rollback-after-yield session** so database mutations never leak between
  tests.
- **ASGI-backed HTTP client** that exercises the FastAPI application stack
  end-to-end without opening a real TCP socket.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.core.database import Base

# In-memory SQLite — every test process gets its own private database.
TEST_DATABASE_URL = "sqlite+aiosqlite://"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop for all async tests.

    Without this fixture pytest-asyncio would create a new loop per test,
    which conflicts with the session-scoped engine and session factory above.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    """Create all tables before the test and drop them afterwards.

    Using ``autouse=True`` ensures every test starts with a pristine schema,
    even if the test author forgets to request the fixture explicitly.
    The ``create_all`` / ``drop_all`` cycle runs inside a transaction via
    ``engine.begin()`` so the DDL is applied and rolled back cleanly.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session that is rolled back after each test.

    The session is yielded for the test to use freely.  After the test
    finishes — whether it passed or raised — the ``rollback()`` call undoes
    all mutations so subsequent tests see a clean database.
    """
    async with test_session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Provide an ``httpx.AsyncClient`` wired directly to the FastAPI app.

    ``ASGITransport`` routes HTTP requests through the ASGI interface in
    process, so the full middleware / dependency-injection stack is exercised
    without binding a real TCP port.
    """
    from hecate.core.database import get_db
    from hecate.core.deps import verify_api_key
    from hecate.main import app

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_verify_api_key() -> str:
        return "test-api-key-123"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[verify_api_key] = override_verify_api_key

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
