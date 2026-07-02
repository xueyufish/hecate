"""add model pricings

Revision ID: e66673e35d7c
Revises: d4d66ddd6959
Create Date: 2026-06-20 11:30:02.848465

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e66673e35d7c"
down_revision: str | None = "d4d66ddd6959"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_WORKSPACE = "00000000-0000-0000-0000-000000000000"
_NOW = datetime.now(UTC)

_SEED_PRICINGS = [
    {
        "model_id": "gpt-4o",
        "display_name": "GPT-4o",
        "input_price_per_1k": 0.0025,
        "output_price_per_1k": 0.01,
    },
    {
        "model_id": "gpt-4o-mini",
        "display_name": "GPT-4o Mini",
        "input_price_per_1k": 0.00015,
        "output_price_per_1k": 0.0006,
    },
    {
        "model_id": "gpt-4-turbo",
        "display_name": "GPT-4 Turbo",
        "input_price_per_1k": 0.01,
        "output_price_per_1k": 0.03,
    },
    {
        "model_id": "claude-3.5-sonnet",
        "display_name": "Claude 3.5 Sonnet",
        "input_price_per_1k": 0.003,
        "output_price_per_1k": 0.015,
    },
    {
        "model_id": "claude-3.5-haiku",
        "display_name": "Claude 3.5 Haiku",
        "input_price_per_1k": 0.00025,
        "output_price_per_1k": 0.00125,
    },
    {
        "model_id": "deepseek-chat",
        "display_name": "DeepSeek Chat",
        "input_price_per_1k": 0.00014,
        "output_price_per_1k": 0.00028,
    },
    {
        "model_id": "deepseek-reasoner",
        "display_name": "DeepSeek Reasoner",
        "input_price_per_1k": 0.00055,
        "output_price_per_1k": 0.00219,
    },
    {
        "model_id": "gemini-2.0-flash",
        "display_name": "Gemini 2.0 Flash",
        "input_price_per_1k": 0.0001,
        "output_price_per_1k": 0.0004,
    },
]


def upgrade() -> None:
    op.create_table(
        "model_pricings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("input_price_per_1k", sa.Float(), nullable=False),
        sa.Column("output_price_per_1k", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("effective_from", sa.DateTime(), nullable=False),
        sa.Column("effective_until", sa.DateTime(), nullable=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_model_pricings_model",
        "model_pricings",
        ["model_id", "workspace_id", "deleted"],
    )
    op.create_index(
        "idx_model_pricings_effective",
        "model_pricings",
        ["effective_from", "effective_until"],
    )

    # Seed pricing data — idempotent: skip if entries already exist
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT COUNT(*) FROM model_pricings WHERE model_id = :model_id"),
        {"model_id": _SEED_PRICINGS[0]["model_id"]},
    ).scalar()

    if not existing:
        for pricing in _SEED_PRICINGS:
            bind.execute(
                sa.text(
                    "INSERT INTO model_pricings "
                    "(id, model_id, display_name, input_price_per_1k, "
                    "output_price_per_1k, currency, effective_from, "
                    "effective_until, workspace_id, created_at, updated_at, "
                    "deleted, deleted_at) "
                    "VALUES (:id, :model_id, :display_name, "
                    ":input_price_per_1k, :output_price_per_1k, :currency, "
                    ":effective_from, :effective_until, :workspace_id, "
                    ":created_at, :updated_at, :deleted, :deleted_at)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "model_id": pricing["model_id"],
                    "display_name": pricing["display_name"],
                    "input_price_per_1k": pricing["input_price_per_1k"],
                    "output_price_per_1k": pricing["output_price_per_1k"],
                    "currency": "USD",
                    "effective_from": _NOW,
                    "effective_until": None,
                    "workspace_id": _DEFAULT_WORKSPACE,
                    "created_at": _NOW,
                    "updated_at": _NOW,
                    "deleted": False,
                    "deleted_at": None,
                },
            )


def downgrade() -> None:
    op.drop_index("idx_model_pricings_effective", table_name="model_pricings")
    op.drop_index("idx_model_pricings_model", table_name="model_pricings")
    op.drop_table("model_pricings")
