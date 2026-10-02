"""Platform adapter for guardrail assembly (step5b).

The kernel's ``assemble_guardrails`` consumes already-loaded ``ToolRule``
rows and never touches the database. This adapter keeps the historical
call shape for the platform chat path: it loads the workspace/per-agent
policy rows over the caller's session, derives the org id, constructs the
ops-side finding writer, and delegates to the kernel facade.
"""

from __future__ import annotations

import uuid
from typing import Any

from hecate_runtime.security.guardrail_assembly import GuardrailBundle, tool_rule_from_policy_row
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def assemble_guardrails(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    guardrail_config: dict | None,
    event_store: Any | None = None,
    session_id: uuid.UUID | None = None,
    dlp_scanner: Any = None,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> GuardrailBundle:
    """Load the agent's tool-policy rows and assemble the guardrail bundle.

    Kept signature-compatible with the pre-5b kernel facade so existing
    callers (chat path) change only their import.

    Args:
        db: Async DB session for the policy lookup.
        workspace_id: Workspace owning the agent; bounds the policy scope.
        agent_id: Agent whose rules augment workspace rules; ``None`` skips
            agent-specific rule loading (e.g. for plain API calls without an
            agent).
        guardrail_config: ``AgentModel.guardrail_config`` dict consumed by
            ``create_security_hooks``.
        event_store: Optional event store; when provided together with
            ``session_id`` and ``workspace_id``, the approval callback
            emits the durable ``APPROVAL_ASKED`` / ``APPROVAL_DECIDED``
            event pair (T2.6).
        session_id: Session id used to anchor approval event emission.
        dlp_scanner: Optional DLP scanner reaching the output/tool-result
            hooks (9.10 wiring).
        org_id: Organization id; auto-derived from ``WorkspaceModel`` when
            not supplied. Required for ``SecurityFindingModel`` persistence.
        user_id: Optional user id; propagated to finding rows when present.
    """
    from hecate_runtime.tool_access import ToolRule

    from hecate.models.tool_policy import ToolPolicyModel, ToolPolicyRuleModel

    rules: list[ToolRule] = []

    # Workspace-level policy baseline (deny-by-default security baseline).
    ws_policy_q = select(ToolPolicyModel).where(
        ToolPolicyModel.workspace_id == workspace_id,
        ToolPolicyModel.deleted_at.is_(None),
    )
    for row in (await db.execute(ws_policy_q)).scalars():
        rule = tool_rule_from_policy_row(row)
        if rule is not None:
            rules.append(rule)

    # Workspace-level + per-agent ToolPolicyRuleModel rows.
    if agent_id is not None:
        rules_q = select(ToolPolicyRuleModel).where(
            ToolPolicyRuleModel.workspace_id == workspace_id,
            ToolPolicyRuleModel.deleted_at.is_(None),
            (ToolPolicyRuleModel.agent_id.is_(None)) | (ToolPolicyRuleModel.agent_id == agent_id),
        )
    else:
        rules_q = select(ToolPolicyRuleModel).where(
            ToolPolicyRuleModel.workspace_id == workspace_id,
            ToolPolicyRuleModel.deleted_at.is_(None),
            ToolPolicyRuleModel.agent_id.is_(None),
        )
    for row in (await db.execute(rules_q)).scalars():
        rule = tool_rule_from_policy_row(row)
        if rule is not None:
            rules.append(rule)

    # Construct the output-side finding writer when context is sufficient.
    # This closes the historical gap where DLP / injection / prompt-leakage
    # findings on the LLM output side never reached SecurityFindingModel.
    finding_writer: Any = None
    if event_store is not None and session_id is not None:
        from hecate.models.workspace import WorkspaceModel
        from hecate.ops.security.findings_writer import SecurityFindingWriter

        if org_id is None:
            ws_row = (
                await db.execute(select(WorkspaceModel).where(WorkspaceModel.id == workspace_id))
            ).scalar_one_or_none()
            org_id = ws_row.org_id if ws_row is not None else None

        finding_writer = SecurityFindingWriter(
            db=db,
            org_id=org_id,
            workspace_id=workspace_id,
            session_id=session_id,
            user_id=user_id,
            event_store=event_store,
        )

    from hecate_runtime.security.guardrail_assembly import assemble_guardrails as kernel_assemble

    return await kernel_assemble(
        rules=rules,
        guardrail_config=guardrail_config,
        event_store=event_store,
        session_id=session_id,
        dlp_scanner=dlp_scanner,
        finding_writer=finding_writer,
        workspace_id=workspace_id,
        agent_id=agent_id,
    )
