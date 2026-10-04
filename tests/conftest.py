"""Shared pytest fixtures for the test suite.

Provides a self-contained test infrastructure that keeps each test isolated:

- **In-memory SQLite** via ``sqlite+aiosqlite://`` so no external database is
  required and tests run fast with zero side-effects.
- **Session-scoped event loop** — ``asyncio_default_*_loop_scope = "session"``
  in ``pyproject.toml`` keeps every async test and fixture on one loop per
  worker, matching the session-scoped engine and session factory below.
- **Session-scoped schema, per-test row cleanup** — ``_create_schema_once``
  builds all tables once per pytest session (per xdist worker);
  ``setup_database`` then clears every table's rows before each test, which
  is far cheaper than a per-test create/drop DDL cycle across ~5,600 tests.
- **Rollback-after-yield session** so database mutations never leak between
  tests.
- **ASGI-backed HTTP client** that exercises the FastAPI application stack
  end-to-end without opening a real TCP socket.
"""

from __future__ import annotations

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
    agent_deployment,
    agent_principal,
    agent_version,
    alert,
    api_key,
    approval,
    backup,
    budget,
    control_command,
    conversation,
    conversation_link,
    document,
    evaluation,
    evidence,
    gateway_target,
    intent_package,
    knowledge,
    memory,
    metric,
    model_pricing,
    model_provider,
    organization,
    platform_event,
    plugin,
    quota,
    run,
    skill,
    skill_version,
    standalone_enrollment,
    task,
    task_lifecycle,
    tool,
    tool_policy,
    trace,
    user,
    workflow,
    workspace,
    workspace_member,
)
from hecate.models import (
    session as models_session,  # noqa: F401  (aliased: fixtures use a local `session`)
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


@pytest_asyncio.fixture(autouse=True, scope="session")
async def _create_schema_once() -> AsyncGenerator[None, None]:
    """Create the full schema once per pytest session (one per xdist worker).

    Must run on the session event loop (the only loop this process uses) so
    the engine's aiosqlite connections stay on a live loop.
    """
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    """Clear every table's rows before the test, children before parents.

    Row-level ``DELETE`` replaces the old per-test ``create_all``/``drop_all``
    cycle, whose DDL dominated suite time. Cleanup runs before the test (not
    after) so the next test starts pristine even if a predecessor crashed.
    """
    async with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


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


# Raw platform admin bootstrap token used by auth-gate tests.
PLATFORM_ADMIN_TEST_TOKEN = "platform-admin-test-token"


@pytest_asyncio.fixture
async def anonymous_client() -> AsyncGenerator[AsyncClient, None]:
    """HTTP client with no authentication overrides — requests are anonymous.

    Only the database is redirected to the in-memory test engine; the auth
    dependency chain runs for real, so credential-less requests exercise the
    401 path end to end.
    """
    from hecate.core.database import get_db
    from hecate.main import app

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def platform_admin_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Enable platform admin token bootstrap for the test.

    Yields the raw admin token to send as ``Authorization: Bearer <token>``;
    settings are restored after the test.
    """
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "PLATFORM_ADMIN_API_KEYS", PLATFORM_ADMIN_TEST_TOKEN)
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_EMAILS", "")
    return PLATFORM_ADMIN_TEST_TOKEN


@pytest.fixture
def platform_admin_email(monkeypatch: pytest.MonkeyPatch) -> str:
    """Enable platform admin email allowlist bootstrap for the test."""
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "PLATFORM_ADMIN_API_KEYS", "")
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_EMAILS", "root@hecate.dev")
    return "root@hecate.dev"


@pytest_asyncio.fixture
async def admin_client(auth_context: AuthContext) -> AsyncGenerator[AsyncClient, None]:
    """Client whose identity satisfies the platform admin gate.

    Provider/model CRUD tests exercise business behavior, not the admin
    gate itself (covered by the dedicated authz matrix in
    ``test_e2e_model_provider.py``). Same overrides as ``client`` plus a
    pass-through for ``require_platform_admin``.
    """
    from hecate.core.config import settings
    from hecate.core.database import get_db
    from hecate.core.deps import get_current_user_id, verify_api_key
    from hecate.core.deps_workspace import get_auth_context, require_platform_admin
    from hecate.main import app

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

    async def override_require_platform_admin() -> AuthContext:
        return auth_context

    settings.HECATE_API_KEYS = "test-api-key-123"
    settings.JWT_SECRET = "test-jwt-secret-for-ci"

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = override_get_auth_context
    app.dependency_overrides[require_platform_admin] = override_require_platform_admin

    async def override_verify_api_key() -> str:
        return "test-api-key-123"

    async def override_get_current_user_id() -> uuid.UUID:
        return auth_context.user_id

    app.dependency_overrides[get_current_user_id] = override_get_current_user_id
    app.dependency_overrides[verify_api_key] = override_verify_api_key

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
