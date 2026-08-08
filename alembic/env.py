from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from hecate.core.database import Base
from hecate.db.migrations.expand_contract import build_split_directives

config = context.config
config.set_main_option(
    "sqlalchemy.url",
    os.getenv("DATABASE_URL", config.get_main_option("sqlalchemy.url")),
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

LOCK_TIMEOUT_SECONDS = os.getenv("ALEMBIC_LOCK_TIMEOUT", "2s")


def process_revision_directives(context_, revision, directives):
    """Alembic autogenerate hook: split mixed ops into expand + contract."""
    if not directives:
        return
    script = directives[0]
    parent_revision = revision[1] if isinstance(revision, tuple) and len(revision) > 1 else None
    if parent_revision is None:
        from alembic.script import ScriptDirectory

        parent_revision = ScriptDirectory.from_config(config).get_current_head()
    split = build_split_directives(
        upgrade_ops=script.upgrade_ops,
        base_rev_id=script.rev_id,
        parent_revision=parent_revision,
        message=getattr(script, "message", ""),
    )
    if split is not None and len(split) == 2:
        directives.clear()
        directives.extend(split)


def _apply_lock_timeout(connection):
    from sqlalchemy import text

    connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT_SECONDS}'"))


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        process_revision_directives=process_revision_directives,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    _apply_lock_timeout(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        process_revision_directives=process_revision_directives,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online():
    import asyncio

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
