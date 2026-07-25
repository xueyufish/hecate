"""rename security_audit_events to tool_decisions

Revision ID: x2d3e4f5a6b7
Revises: w1c2d3e4f5a6
Create Date: 2026-07-25

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "x2d3e4f5a6b7"
down_revision = "w1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Rename table security_audit_events → tool_decisions."""
    op.rename_table("security_audit_events", "tool_decisions")
    # Drop old indexes (names include old table name)
    op.drop_index("ix_security_audit_agent_ts", table_name="tool_decisions")
    op.drop_index("ix_security_audit_workspace_ts", table_name="tool_decisions")
    op.drop_index("ix_security_audit_decision_ts", table_name="tool_decisions")
    # Create new indexes with updated names
    op.create_index("ix_tool_decision_agent_ts", "tool_decisions", ["agent_id", "timestamp"])
    op.create_index("ix_tool_decision_workspace_ts", "tool_decisions", ["workspace_id", "timestamp"])
    op.create_index("ix_tool_decision_decision_ts", "tool_decisions", ["decision", "timestamp"])


def downgrade() -> None:
    """Revert: tool_decisions → security_audit_events."""
    op.drop_index("ix_tool_decision_decision_ts", table_name="tool_decisions")
    op.drop_index("ix_tool_decision_workspace_ts", table_name="tool_decisions")
    op.drop_index("ix_tool_decision_agent_ts", table_name="tool_decisions")
    op.create_index("ix_security_audit_decision_ts", "security_audit_events", ["decision", "timestamp"])
    op.create_index("ix_security_audit_workspace_ts", "security_audit_events", ["workspace_id", "timestamp"])
    op.create_index("ix_security_audit_agent_ts", "security_audit_events", ["agent_id", "timestamp"])
    op.rename_table("tool_decisions", "security_audit_events")
