"""Tests for default DLP policy rules and idempotent installation."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dlp import DLPPolicyModel
from hecate.ops.dlp.defaults import (
    DEFAULT_RULES,
    create_default_policies_for_org,
    default_rules_already_installed,
)
from hecate.ops.dlp.result import DLPAction


class TestDefaultRulesSpec:
    def test_has_secrets_rules(self) -> None:
        secrets = [r for r in DEFAULT_RULES if "TOKEN" in r.entity_type or r.entity_type == "PRIVATE_KEY"]
        assert len(secrets) >= 5

    def test_secrets_rules_are_block_and_locked(self) -> None:
        secrets = [r for r in DEFAULT_RULES if "TOKEN" in r.entity_type or r.entity_type == "PRIVATE_KEY"]
        for r in secrets:
            assert r.action == DLPAction.BLOCK
            assert r.is_locked is True

    def test_has_pii_rules(self) -> None:
        pii = [r for r in DEFAULT_RULES if r.entity_type in {"SSN", "CREDIT_CARD", "CHINA_ID_CARD"}]
        assert len(pii) >= 3

    def test_pii_rules_are_mask(self) -> None:
        pii = [r for r in DEFAULT_RULES if r.entity_type in {"SSN", "CREDIT_CARD", "CHINA_ID_CARD"}]
        for r in pii:
            assert r.action == DLPAction.MASK

    def test_has_context_rules(self) -> None:
        context = [r for r in DEFAULT_RULES if r.entity_type in {"EMAIL", "PHONE", "IP_ADDRESS"}]
        assert len(context) >= 3

    def test_context_rules_are_audit(self) -> None:
        context = [r for r in DEFAULT_RULES if r.entity_type in {"EMAIL", "PHONE", "IP_ADDRESS"}]
        for r in context:
            assert r.action == DLPAction.AUDIT


class TestCreateDefaultPoliciesForOrg:
    async def test_creates_rules_for_new_org(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        rows = await create_default_policies_for_org(db_session, org_id)
        assert len(rows) == len(DEFAULT_RULES)
        for row in rows:
            assert row.org_id == org_id
            assert row.workspace_id is None
            assert row.agent_id is None
            assert row.enabled is True

    async def test_is_idempotent(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        first = await create_default_policies_for_org(db_session, org_id)
        second = await create_default_policies_for_org(db_session, org_id)
        assert len(first) == len(DEFAULT_RULES)
        assert second == []

    async def test_does_not_clobber_existing_org_specific_rules(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        existing = DLPPolicyModel(
            org_id=org_id,
            workspace_id=None,
            agent_id=None,
            entity_type="CUSTOM",
            direction="llm_output",
            action=DLPAction.BLOCK.value,
            enabled=True,
        )
        db_session.add(existing)
        await db_session.flush()

        assert await default_rules_already_installed(db_session, org_id) is True
        rows = await create_default_policies_for_org(db_session, org_id)
        assert rows == []

    async def test_separate_orgs_get_separate_defaults(self, db_session: AsyncSession) -> None:
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()
        rows_a = await create_default_policies_for_org(db_session, org_a)
        rows_b = await create_default_policies_for_org(db_session, org_b)
        assert len(rows_a) == len(DEFAULT_RULES)
        assert len(rows_b) == len(DEFAULT_RULES)
        for row in rows_a:
            assert row.org_id == org_a
        for row in rows_b:
            assert row.org_id == org_b

    async def test_default_rules_already_installed_false_for_fresh_org(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        assert await default_rules_already_installed(db_session, org_id) is False

    async def test_default_rules_already_installed_true_after_install(self, db_session: AsyncSession) -> None:
        org_id = uuid.uuid4()
        await create_default_policies_for_org(db_session, org_id)
        assert await default_rules_already_installed(db_session, org_id) is True
