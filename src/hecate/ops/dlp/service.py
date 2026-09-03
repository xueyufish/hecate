"""DLP service — CRUD, policy resolution, and dry-run scan.

Wraps the DLP ORM models (:class:`DLPPolicyModel`,
:class:`DLPCustomRegexModel`, :class:`DLPDictionaryModel`) and exposes a
small, intentional surface for the REST API. Most callers should use
:class:`DLPPolicyResolver` and :class:`DLPScanner` directly; this
service exists for configuration management and the dry-run endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dlp import (
    DLPCustomRegexModel,
    DLPDictionaryModel,
    DLPPolicyModel,
)
from hecate.ops.dlp.policy import (
    DLPPolicyResolver,
    DLPPolicyRule,
    PolicyScope,
)
from hecate.ops.dlp.recognizer import DLPRecognizerRegistry
from hecate.ops.dlp.result import DLPAction, DLPResult
from hecate.ops.dlp.scanner import DLPScanner

_DLP_DIRECTIONS = ("llm_input", "llm_output", "tool_input", "tool_output", "mcp_response")


class DLPService:
    """DLP CRUD + resolution + dry-run scan."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Policy CRUD
    # ------------------------------------------------------------------

    async def list_policies(
        self,
        *,
        org_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        direction: str | None = None,
        enabled_only: bool = True,
    ) -> list[DLPPolicyModel]:
        stmt = select(DLPPolicyModel).where(DLPPolicyModel.deleted == False)  # noqa: E712
        if enabled_only:
            stmt = stmt.where(DLPPolicyModel.enabled.isnot(False))
        if org_id:
            stmt = stmt.where(DLPPolicyModel.org_id == org_id)
        if workspace_id:
            stmt = stmt.where(DLPPolicyModel.workspace_id == workspace_id)
        if agent_id:
            stmt = stmt.where(DLPPolicyModel.agent_id == agent_id)
        if direction:
            stmt = stmt.where(DLPPolicyModel.direction == direction)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_policy(self, policy_id: uuid.UUID) -> DLPPolicyModel | None:
        policy = await self._db.get(DLPPolicyModel, policy_id)
        if policy is None or policy.deleted:
            return None
        return policy

    async def create_policy(
        self,
        *,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
        agent_id: uuid.UUID | None,
        entity_type: str,
        direction: str,
        action: str,
        mask_format: str | None = None,
        is_locked: bool = False,
        enabled: bool = True,
    ) -> DLPPolicyModel:
        policy = DLPPolicyModel(
            org_id=org_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            entity_type=entity_type,
            direction=direction,
            action=action,
            mask_format=mask_format,
            is_locked=is_locked,
            enabled=enabled,
        )
        self._db.add(policy)
        await self._db.flush()
        return policy

    async def update_policy(
        self,
        policy_id: uuid.UUID,
        *,
        action: str | None = None,
        mask_format: str | None = None,
        is_locked: bool | None = None,
        enabled: bool | None = None,
    ) -> DLPPolicyModel | None:
        policy = await self._db.get(DLPPolicyModel, policy_id)
        if policy is None:
            return None
        if action is not None:
            policy.action = action
        if mask_format is not None:
            policy.mask_format = mask_format
        if is_locked is not None:
            policy.is_locked = is_locked
        if enabled is not None:
            policy.enabled = enabled
        await self._db.flush()
        return policy

    async def delete_policy(self, policy_id: uuid.UUID) -> bool:
        policy = await self._db.get(DLPPolicyModel, policy_id)
        if policy is None or policy.deleted:
            return False
        policy.deleted = True
        policy.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    # ------------------------------------------------------------------
    # Custom regex CRUD
    # ------------------------------------------------------------------

    async def list_custom_regex(
        self,
        *,
        org_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        enabled_only: bool = True,
    ) -> list[DLPCustomRegexModel]:
        stmt = select(DLPCustomRegexModel).where(
            DLPCustomRegexModel.deleted == False  # noqa: E712
        )
        if enabled_only:
            stmt = stmt.where(DLPCustomRegexModel.enabled.isnot(False))
        if org_id:
            stmt = stmt.where(DLPCustomRegexModel.org_id == org_id)
        if workspace_id:
            stmt = stmt.where(DLPCustomRegexModel.workspace_id == workspace_id)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def create_custom_regex(
        self,
        *,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
        name: str,
        pattern: str,
        entity_type: str,
        enabled: bool = True,
    ) -> DLPCustomRegexModel:
        rec = DLPCustomRegexModel(
            org_id=org_id,
            workspace_id=workspace_id,
            name=name,
            pattern=pattern,
            entity_type=entity_type,
            enabled=enabled,
        )
        self._db.add(rec)
        await self._db.flush()
        return rec

    async def delete_custom_regex(self, regex_id: uuid.UUID) -> bool:
        rec = await self._db.get(DLPCustomRegexModel, regex_id)
        if rec is None or rec.deleted:
            return False
        rec.deleted = True
        rec.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    # ------------------------------------------------------------------
    # Dictionary CRUD
    # ------------------------------------------------------------------

    async def list_dictionaries(
        self,
        *,
        org_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        enabled_only: bool = True,
    ) -> list[DLPDictionaryModel]:
        stmt = select(DLPDictionaryModel).where(
            DLPDictionaryModel.deleted == False  # noqa: E712
        )
        if enabled_only:
            stmt = stmt.where(DLPDictionaryModel.enabled.isnot(False))
        if org_id:
            stmt = stmt.where(DLPDictionaryModel.org_id == org_id)
        if workspace_id:
            stmt = stmt.where(DLPDictionaryModel.workspace_id == workspace_id)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def create_dictionary(
        self,
        *,
        org_id: uuid.UUID | None,
        workspace_id: uuid.UUID | None,
        name: str,
        entity_type: str,
        terms: list[str],
        case_sensitive: bool = False,
        enabled: bool = True,
    ) -> DLPDictionaryModel:
        rec = DLPDictionaryModel(
            org_id=org_id,
            workspace_id=workspace_id,
            name=name,
            entity_type=entity_type,
            terms=list(terms),
            case_sensitive=case_sensitive,
            enabled=enabled,
        )
        self._db.add(rec)
        await self._db.flush()
        return rec

    async def delete_dictionary(self, dictionary_id: uuid.UUID) -> bool:
        rec = await self._db.get(DLPDictionaryModel, dictionary_id)
        if rec is None or rec.deleted:
            return False
        rec.deleted = True
        rec.deleted_at = datetime.now(UTC)
        await self._db.flush()
        return True

    # ------------------------------------------------------------------
    # Policy resolution + dry-run scan
    # ------------------------------------------------------------------

    async def build_resolver(
        self,
        *,
        org_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
    ) -> DLPPolicyResolver:
        """Materialize a :class:`DLPPolicyResolver` from current DB state."""
        policies = await self.list_policies(
            org_id=org_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            enabled_only=True,
        )
        rules = [_to_policy_rule(p) for p in policies if p.action in {"allow", "block", "mask", "audit"}]
        return DLPPolicyResolver(rules)

    async def dry_run_scan(
        self,
        text: str,
        direction: str,
        *,
        org_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        registry: DLPRecognizerRegistry | None = None,
    ) -> DLPResult:
        """Run a one-off scan against the current policy + optional registry.

        ``registry`` defaults to an empty registry (no recognizers) so
        the endpoint can dry-run policy resolution without invoking
        heavy detection. Callers wanting a full scan should pass a
        registry constructed from the DB (see :class:`DLPRegistryFactory`).
        """
        resolver = await self.build_resolver(org_id=org_id, workspace_id=workspace_id, agent_id=agent_id)
        scanner = DLPScanner(registry or DLPRecognizerRegistry(), resolver)
        return scanner.scan(
            text,
            direction,
            agent_id=agent_id,
            workspace_id=workspace_id,
            org_id=org_id,
        )


def _to_policy_rule(policy: DLPPolicyModel) -> DLPPolicyRule:
    """Convert a :class:`DLPPolicyModel` row to a runtime rule."""
    scope, scope_id = _infer_scope(policy)
    return DLPPolicyRule(
        entity_type=policy.entity_type,
        direction=policy.direction,
        action=DLPAction(policy.action),
        scope=scope,
        scope_id=scope_id,
        is_locked=policy.is_locked,
        mask_format=policy.mask_format,
    )


def _infer_scope(policy: DLPPolicyModel) -> tuple[PolicyScope, uuid.UUID | None]:
    """Map a DB row's three optional scope IDs to (PolicyScope, scope_id)."""
    if policy.agent_id:
        return PolicyScope.AGENT, policy.agent_id
    if policy.workspace_id:
        return PolicyScope.WORKSPACE, policy.workspace_id
    if policy.org_id:
        return PolicyScope.ORG, policy.org_id
    return PolicyScope.DEFAULT, None


def known_entity_types() -> list[str]:
    """Return the canonical entity-type names for the /dlp/entities endpoint."""
    from hecate.ops.dlp.recognizers.regex import RegexRecognizer

    return sorted(set(RegexRecognizer().supported_entities))


def supported_directions() -> list[str]:
    """Return the supported DLP scan directions."""
    return list(_DLP_DIRECTIONS)
