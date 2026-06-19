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
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.core.auth_context import AuthContext
from hecate.core.database import Base
from hecate.models import (  # noqa: F401
    agent,
    api_key,
    approval,
    budget,
    checkpoint,
    conversation,
    document,
    evaluation,
    evidence,
    knowledge,
    memory,
    message,
    metric,
    model_provider,
    organization,
    skill,
    tool,
    tool_policy,
    trace,
    user,
    workflow,
    workspace,
    workspace_member,
)
from hecate.models.api_key import ApiKeyModel, ApiKeyScope
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

# In-memory SQLite — every test process gets its own private database.
TEST_DATABASE_URL = "sqlite+aiosqlite://"

test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
test_session_factory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Default workspace UUID for tests
DEFAULT_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


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
async def default_org(db_session: AsyncSession) -> OrganizationModel:
    """Create a default organization for testing."""
    org = OrganizationModel(
        id=DEFAULT_WORKSPACE_ID,
        name="Test Organization",
        slug="test-org",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()
    return org


@pytest_asyncio.fixture
async def default_workspace(db_session: AsyncSession, default_org: OrganizationModel) -> WorkspaceModel:
    """Create a default workspace for testing."""
    ws = WorkspaceModel(
        id=DEFAULT_WORKSPACE_ID,
        org_id=default_org.id,
        name="Default Workspace",
        slug="default",
    )
    db_session.add(ws)
    await db_session.flush()
    return ws


@pytest_asyncio.fixture
async def test_user_id() -> uuid.UUID:
    """Return a test user ID."""
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def workspace_member_fixture(
    db_session: AsyncSession,
    default_workspace: WorkspaceModel,
    test_user_id: uuid.UUID,
) -> WorkspaceMemberModel:
    """Create a workspace member with admin role for testing."""
    member = WorkspaceMemberModel(
        user_id=test_user_id,
        workspace_id=default_workspace.id,
        role=WorkspaceRole.ADMIN,
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest_asyncio.fixture
async def test_api_key(
    db_session: AsyncSession,
    default_workspace: WorkspaceModel,
    test_user_id: uuid.UUID,
) -> ApiKeyModel:
    """Create a test API key for testing."""
    import hashlib

    raw_key = "hcat_test1234567890abcdef12345678"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    api_key_obj = ApiKeyModel(
        name="Test API Key",
        key_hash=key_hash,
        key_prefix="hcat_tes",
        scope=ApiKeyScope.WORKSPACE,
        workspace_id=default_workspace.id,
        created_by=test_user_id,
        is_active=True,
    )
    db_session.add(api_key_obj)
    await db_session.flush()
    return api_key_obj


@pytest_asyncio.fixture
def auth_context(test_user_id: uuid.UUID, default_workspace: WorkspaceModel) -> AuthContext:
    """Create a test AuthContext for dependency injection."""
    return AuthContext(
        user_id=test_user_id,
        org_id=default_workspace.org_id,
        workspace_id=default_workspace.id,
        role=WorkspaceRole.ADMIN,
        auth_method="jwt",
        api_key_scope=None,
    )


@pytest_asyncio.fixture
def system_auth_context(test_user_id: uuid.UUID) -> AuthContext:
    """Create a system-scope AuthContext for testing."""
    return AuthContext(
        user_id=test_user_id,
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


@pytest_asyncio.fixture
async def client(auth_context: AuthContext) -> AsyncGenerator[AsyncClient, None]:
    """Provide an ``httpx.AsyncClient`` wired directly to the FastAPI app.

    ``ASGITransport`` routes HTTP requests through the ASGI interface in
    process, so the full middleware / dependency-injection stack is exercised
    without binding a real TCP port.
    """
    from hecate.core.config import settings
    from hecate.core.database import get_db
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_auth_context() -> AuthContext:
        return auth_context

    async def override_get_current_user_id() -> uuid.UUID:
        return auth_context.user_id

    settings.HECATE_API_KEYS = "test-api-key-123"
    settings.JWT_SECRET = "test-jwt-secret-for-ci"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = override_get_auth_context

    # Also override old deps for backward compatibility with existing tests
    from hecate.core.deps import get_current_user_id, verify_api_key

    async def override_verify_api_key() -> str:
        return "test-api-key-123"

    app.dependency_overrides[get_current_user_id] = override_get_current_user_id
    app.dependency_overrides[verify_api_key] = override_verify_api_key

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
