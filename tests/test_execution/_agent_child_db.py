"""Database wiring shared by the Agent child subprocess helpers.

Connection material travels through the child process environment, never
through ``argv``: ``str(URL)`` masks the password (so a PostgreSQL child
could not even authenticate), and argv is visible to every process on the
host. Two variables serve both tiers:

- ``HECATE_AGENT_CHILD_DURABLE_DSN`` — the durable store URL (``sqlite:///``
  on the default tier; ``postgresql+psycopg://`` on PostgreSQL, already
  carrying the per-test schema in its libpq ``options``).
- ``HECATE_AGENT_CHILD_SCHEMA`` — the per-test PostgreSQL schema (empty on
  SQLite) for the asyncpg ORM engine, which cannot take libpq ``options``.

The module has no pytest dependency so ``python -m`` helpers can import it.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy.engine import make_url

DURABLE_DSN_ENV = "HECATE_AGENT_CHILD_DURABLE_DSN"
SCHEMA_ENV = "HECATE_AGENT_CHILD_SCHEMA"


def child_environment(harness: dict[str, Any]) -> dict[str, str]:
    """Environment for a subprocess that must share the harness database."""

    env = os.environ.copy()
    env[DURABLE_DSN_ENV] = harness["durable_dsn"]
    env[SCHEMA_ENV] = harness["schema"] or ""
    # The tier switch must not leak into children: they bind to the explicit
    # DSN of THIS test's schema, never to a fresh schema of their own.
    env.pop("HECATE_STEP6_POSTGRES_URL", None)
    return env


def durable_dsn() -> str:
    return os.environ[DURABLE_DSN_ENV]


def is_postgres() -> bool:
    return durable_dsn().startswith("postgresql")


def backend_name() -> str:
    return "postgresql" if is_postgres() else "sqlite"


def open_store_and_sessions() -> tuple[Any, Any, Any]:
    """``(store, async_engine, session_factory)`` bound to the shared database.

    PostgreSQL keeps the harness split: the durable store stays on sync
    psycopg (callers run its blocking calls in worker threads), the ORM uses
    asyncpg pinned to the test schema through ``server_settings``.
    """

    from hecate_durable.storage import SqlDurableStore
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    dsn = durable_dsn()
    application = os.environ.get("HECATE_AGENT_CHILD_APPLICATION_NAME")
    if application and dsn.startswith("postgresql"):
        dsn = make_url(dsn).update_query_dict({"application_name": application}).render_as_string(hide_password=False)
    store = SqlDurableStore(dsn, source="platform")
    if dsn.startswith("postgresql"):
        schema = os.environ.get(SCHEMA_ENV) or None
        async_url = make_url(dsn).difference_update_query(["options"]).set(drivername="postgresql+asyncpg")
        connect_args = {"server_settings": {"search_path": schema}} if schema else {}
        if application:
            connect_args.setdefault("server_settings", {})["application_name"] = application
            async_url = async_url.difference_update_query(["application_name"])
        engine = create_async_engine(async_url, connect_args=connect_args)
    else:
        engine = create_async_engine(dsn.replace("sqlite:", "sqlite+aiosqlite:", 1))
    return store, engine, async_sessionmaker(engine, expire_on_commit=False)
