"""Default DLP policy rules and idempotent org-level installation.

Per design.md §D5, new orgs get a conservative default policy set:

* Secrets (AWS/GCP/PrivateKey/JWT/GitHub) → BLOCK with ``is_locked=True`` —
  red lines the security team sets and that workspace/agent scopes cannot
  override.
* PII (SSN, CREDIT_CARD, CHINA_ID_CARD) → MASK — redact the obvious
  sensitive identifiers but allow the content through.
* Context (EMAIL, PHONE, IP_ADDRESS) → AUDIT — log detections without
  blocking; gives the org a chance to monitor volume before tightening.

``create_default_policies_for_org`` is idempotent: running it twice for
the same org is a no-op the second time. The ``is_locked`` flag on
secrets rules means workspaces/agents cannot relax them without a
direct admin migration.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dlp import DLPPolicyModel
from hecate.ops.dlp.result import DLPAction


@dataclass(frozen=True)
class _DefaultRuleSpec:
    entity_type: str
    direction: str
    action: DLPAction
    is_locked: bool = False


DEFAULT_RULES: list[_DefaultRuleSpec] = [
    # Secrets — BLOCK + locked. Red lines the org cannot relax.
    _DefaultRuleSpec("AWS_ACCESS_KEY", "llm_output", DLPAction.BLOCK, True),
    _DefaultRuleSpec("AWS_ACCESS_KEY", "llm_input", DLPAction.BLOCK, True),
    _DefaultRuleSpec("GCP_SERVICE_KEY", "llm_output", DLPAction.BLOCK, True),
    _DefaultRuleSpec("GCP_SERVICE_KEY", "llm_input", DLPAction.BLOCK, True),
    _DefaultRuleSpec("PRIVATE_KEY", "llm_output", DLPAction.BLOCK, True),
    _DefaultRuleSpec("PRIVATE_KEY", "llm_input", DLPAction.BLOCK, True),
    _DefaultRuleSpec("JWT_TOKEN", "llm_output", DLPAction.BLOCK, True),
    _DefaultRuleSpec("JWT_TOKEN", "llm_input", DLPAction.BLOCK, True),
    _DefaultRuleSpec("GITHUB_TOKEN", "llm_output", DLPAction.BLOCK, True),
    _DefaultRuleSpec("GITHUB_TOKEN", "llm_input", DLPAction.BLOCK, True),
    # PII — MASK. Obvious sensitive identifiers, redact before egress.
    _DefaultRuleSpec("SSN", "llm_output", DLPAction.MASK),
    _DefaultRuleSpec("SSN", "tool_output", DLPAction.MASK),
    _DefaultRuleSpec("CREDIT_CARD", "llm_output", DLPAction.MASK),
    _DefaultRuleSpec("CREDIT_CARD", "tool_output", DLPAction.MASK),
    _DefaultRuleSpec("CHINA_ID_CARD", "llm_output", DLPAction.MASK),
    _DefaultRuleSpec("CHINA_ID_CARD", "tool_output", DLPAction.MASK),
    # Context — AUDIT. Log detections; let the org monitor before tightening.
    _DefaultRuleSpec("EMAIL", "llm_output", DLPAction.AUDIT),
    _DefaultRuleSpec("PHONE", "llm_output", DLPAction.AUDIT),
    _DefaultRuleSpec("IP_ADDRESS", "llm_output", DLPAction.AUDIT),
]


async def default_rules_already_installed(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> bool:
    """Return True if any default rule already exists for this org.

    Used as the idempotency guard for :func:`create_default_policies_for_org`.
    """
    stmt = (
        select(DLPPolicyModel.id)
        .where(DLPPolicyModel.org_id == org_id)
        .where(DLPPolicyModel.workspace_id.is_(None))
        .where(DLPPolicyModel.agent_id.is_(None))
        .where(DLPPolicyModel.deleted == False)  # noqa: E712
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def create_default_policies_for_org(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> list[DLPPolicyModel]:
    """Install the default policy set for ``org_id`` (idempotent).

    Returns the list of newly-created :class:`DLPPolicyModel` rows. If
    defaults were already installed, returns ``[]`` without making
    changes.
    """
    if await default_rules_already_installed(db, org_id):
        return []

    rows = [
        DLPPolicyModel(
            org_id=org_id,
            workspace_id=None,
            agent_id=None,
            entity_type=spec.entity_type,
            direction=spec.direction,
            action=spec.action.value,
            is_locked=spec.is_locked,
            enabled=True,
        )
        for spec in DEFAULT_RULES
    ]
    db.add_all(rows)
    await db.flush()
    return rows
