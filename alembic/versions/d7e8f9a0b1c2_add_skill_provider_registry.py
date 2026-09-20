"""Add skill provider registry columns and coexistence indexes (5.9-enh).

Revision ID: d7e8f9a0b1c2
Revises: f6a5b4c3d2e1
Create Date: 2026-09-20

Data-model extensions for the skill-provider-registry OpenSpec change:

- ``skills.provider`` (String(20), nullable) — rank-precedence
  classification (``bundled``/``user``/``project``; ``custom`` reserved).
  NULL for plugin-sourced rows, which stay outside rank competition.
- ``skills.trust_tier`` (String(20), NOT NULL, default ``community``) —
  ``official`` for bundled rows, ``community`` otherwise (plugin rows
  inherit their package tier at the application layer).
- ``skills.model_invocable`` / ``skills.user_invocable`` (Boolean, NOT
  NULL, default true) — invocation-policy switches.
- ``skills.content_hash`` (String(64), nullable) — sha256 over the same
  content-field set as agent-version reference manifests (name,
  instructions, allowed_tools, scripts, references).

Index changes: the previous unique index ``(workspace_id, name, deleted,
deleted_at)`` is replaced by a coexistence unique index that adds
``provider`` plus a partial unique index for ``provider IS NULL`` rows
(plugin provenance keeps today's one-row-per-name semantics). Same-name
skills of different providers may now coexist in one workspace.

The content-hash backfill freezes the hashing logic inline (canonical
JSON with ``sort_keys``/``ensure_ascii=False``/``default=str``, sha256
over UTF-8) so this migration stays byte-identical to the hashes the
application writes via ``hecate.core.canonical_hash.canonical_hash``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "d7e8f9a0b1c2"
down_revision = "f6a5b4c3d2e1"
branch_labels = None
depends_on = None

_OLD_INDEX = "idx_skills_workspace_name"
_COEXISTENCE_INDEX = "idx_skills_ws_name_provider"
_PLUGIN_PARTIAL_INDEX = "idx_skills_ws_name_unranked"

_PROVIDER_BY_SOURCE = {"system": "bundled", "user": "user", "project": "project"}


def _content_hash(name: str, instructions: str, allowed_tools: Any, scripts: Any, references: Any) -> str:
    """Frozen twin of ``hecate.core.canonical_hash`` over the ref-manifest field set."""
    payload = json.dumps(
        {
            "name": name,
            "instructions": instructions,
            "allowed_tools": allowed_tools,
            "scripts": scripts,
            "references": references,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_json(value: Any) -> Any:
    """Normalize a JSON column read through a raw connection.

    Some drivers (asyncpg, raw SQLite) return JSON columns as strings;
    the application-level hash is computed over parsed structures, so
    string payloads are decoded before hashing.
    """
    if isinstance(value, str):
        return json.loads(value)
    return value


def _backfill_rows() -> None:
    """Backfill provider, trust_tier and content_hash for existing rows."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE skills SET provider = CASE source "
            "WHEN 'system' THEN 'bundled' WHEN 'user' THEN 'user' "
            "WHEN 'project' THEN 'project' ELSE NULL END"
        )
    )
    bind.execute(
        sa.text("UPDATE skills SET trust_tier = 'official' WHERE provider = 'bundled'")
    )
    rows = bind.execute(
        sa.text(
            'SELECT id, name, instructions, allowed_tools, scripts, "references" FROM skills'
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE skills SET content_hash = :h WHERE id = :id"),
            {
                "h": _content_hash(
                    row.name,
                    row.instructions,
                    _as_json(row.allowed_tools),
                    _as_json(row.scripts),
                    _as_json(row.references),
                ),
                "id": str(row.id),
            },
        )


def upgrade() -> None:
    with op.batch_alter_table("skills") as batch:
        batch.add_column(sa.Column("provider", sa.String(20), nullable=True))
        batch.add_column(
            sa.Column(
                "trust_tier",
                sa.String(20),
                nullable=False,
                server_default="community",
            )
        )
        batch.add_column(
            sa.Column(
                "model_invocable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "user_invocable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))

    _backfill_rows()

    op.create_index(
        _COEXISTENCE_INDEX,
        "skills",
        ["workspace_id", "name", "provider", "deleted", "deleted_at"],
        unique=True,
    )
    op.create_index(
        _PLUGIN_PARTIAL_INDEX,
        "skills",
        ["workspace_id", "name", "deleted", "deleted_at"],
        unique=True,
        postgresql_where=sa.text("provider IS NULL"),
        sqlite_where=sa.text("provider IS NULL"),
    )
    op.drop_index(_OLD_INDEX, table_name="skills")


def downgrade() -> None:
    """Restore single-row-per-name semantics.

    Same-name rows of different providers may exist by now; collapsing
    them keeps the highest-precedence row (project > user > bundled,
    oldest first within a rank) per (workspace, name, deleted) group so
    the pre-5.9-enh unique index can be recreated.
    """
    op.create_index(
        _OLD_INDEX,
        "skills",
        ["workspace_id", "name", "deleted", "deleted_at"],
        unique=True,
    )
    op.drop_index(_PLUGIN_PARTIAL_INDEX, table_name="skills")
    op.drop_index(_COEXISTENCE_INDEX, table_name="skills")

    bind = op.get_bind()
    # Candidate selection runs in a subquery (proper aliasing in both
    # PostgreSQL and SQLite); the outer DELETE is a plain id IN (...) form.
    bind.execute(
        sa.text(
            """
            DELETE FROM skills WHERE id IN (
                SELECT s.id FROM skills AS s
                WHERE s.provider IS NOT NULL
                  AND EXISTS (
                    SELECT 1 FROM skills AS t
                    WHERE t.workspace_id = s.workspace_id
                      AND t.name = s.name
                      AND t.deleted = s.deleted
                      AND t.provider IS NOT NULL
                      AND (
                        CASE t.provider
                          WHEN 'project' THEN 0 WHEN 'user' THEN 1 WHEN 'bundled' THEN 2 ELSE 3 END
                          < CASE s.provider
                          WHEN 'project' THEN 0 WHEN 'user' THEN 1 WHEN 'bundled' THEN 2 ELSE 3 END
                        )
                        OR (
                          CASE t.provider
                            WHEN 'project' THEN 0 WHEN 'user' THEN 1 WHEN 'bundled' THEN 2 ELSE 3 END
                            = CASE s.provider
                            WHEN 'project' THEN 0 WHEN 'user' THEN 1 WHEN 'bundled' THEN 2 ELSE 3 END
                            AND t.created_at < s.created_at
                          )
                  )
                )
            """
        )
    )
    # Plugin rows sharing a name with a surviving manual row must go to
    # restore the old one-row-per-name uniqueness.
    bind.execute(
        sa.text(
            """
            DELETE FROM skills WHERE id IN (
                SELECT s.id FROM skills AS s
                WHERE s.provider IS NULL
                  AND EXISTS (
                    SELECT 1 FROM skills AS t
                    WHERE t.workspace_id = s.workspace_id
                      AND t.name = s.name
                      AND t.deleted = s.deleted
                      AND t.provider IS NOT NULL
                  )
                )
            """
        )
    )

    with op.batch_alter_table("skills") as batch:
        batch.drop_column("content_hash")
        batch.drop_column("user_invocable")
        batch.drop_column("model_invocable")
        batch.drop_column("trust_tier")
        batch.drop_column("provider")
