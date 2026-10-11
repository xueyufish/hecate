"""Execution-owned principal lifecycle view for action authorization."""

from typing import Any

from hecate_governance.policy import RevocationView
from hecate_governance.principal import ActionPrincipal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class PrincipalLifecycleRevocation(RevocationView):
    """Known-revocation view backed by the agent principal lifecycle."""

    def __init__(self, db: AsyncSession, agent_id: Any = None) -> None:
        self._db = db
        self._agent_id = agent_id

    async def is_revoked(self, principal: ActionPrincipal) -> bool:
        if self._agent_id is None or self._db is None:
            return False
        from hecate.models.agent_principal import AgentPrincipalModel, PrincipalLifecycle

        result = await self._db.execute(
            select(AgentPrincipalModel.lifecycle).where(AgentPrincipalModel.agent_id == self._agent_id)
        )
        row = result.first()
        return row is not None and row.lifecycle == PrincipalLifecycle.REVOKED
