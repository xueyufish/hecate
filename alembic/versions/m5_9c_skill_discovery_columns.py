"""Skill auto-detection: agents.skill_discovery_enabled + skill_usage_events.detected_via (5.9c).

Revision ID: m5_9c_skill_discovery
Revises: m4_23_namespace_team_actor
Create Date: 2026-09-23

Fully additive — two nullable columns, no backfill needed:

- ``agents.skill_discovery_enabled`` — three-state discovery override
  (5.9c): ``NULL`` follows the workspace policy, ``TRUE``/``FALSE`` are
  explicit opt-in/opt-out. The layers only narrow, never widen.
- ``skill_usage_events.detected_via`` — usage-provenance marker
  (``"bound"`` / ``"auto_detected"``); ``NULL`` on pre-5.9c rows is read
  as ``"bound"`` by the read paths.

Both columns are inert until the discovery feature is switched on
(``settings.SKILL_DISCOVERY_ENABLED`` plus the workspace policy), so the
migration alone changes no behaviour.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m5_9c_skill_discovery"
down_revision = "m4_23_namespace_team_actor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the two nullable discovery columns."""
    op.add_column(
        "agents",
        sa.Column("skill_discovery_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "skill_usage_events",
        sa.Column("detected_via", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    """Reverse the skill auto-detection columns."""
    op.drop_column("skill_usage_events", "detected_via")
    op.drop_column("agents", "skill_discovery_enabled")
