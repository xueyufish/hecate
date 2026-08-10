"""Tests for DLPService — CRUD + policy resolution + dry-run scan."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dlp import (
    DLPPolicyModel,
)
from hecate.services.security.dlp.result import DLPAction
from hecate.services.security.dlp.service import (
    DLPService,
    known_entity_types,
    supported_directions,
)


class TestDLPPolicyCRUD:
    async def test_create_and_get(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        org_id = uuid.uuid4()
        policy = await service.create_policy(
            org_id=org_id,
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="mask",
            is_locked=False,
        )
        assert policy.id is not None
        assert policy.org_id == org_id
        assert policy.action == "mask"
        assert policy.enabled is True

        fetched = await service.get_policy(policy.id)
        assert fetched is not None
        assert fetched.id == policy.id

    async def test_get_missing_returns_none(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        assert await service.get_policy(uuid.uuid4()) is None

    async def test_list_filters_by_org(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()
        await service.create_policy(
            org_id=org_a,
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="mask",
        )
        await service.create_policy(
            org_id=org_b,
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="block",
        )
        list_a = await service.list_policies(org_id=org_a)
        assert len(list_a) == 1
        assert list_a[0].action == "mask"

    async def test_list_excludes_disabled_by_default(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        await service.create_policy(
            org_id=uuid.uuid4(),
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="mask",
            enabled=False,
        )
        assert await service.list_policies() == []
        all_policies = await service.list_policies(enabled_only=False)
        assert len(all_policies) == 1

    async def test_update_modifies_fields(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        policy = await service.create_policy(
            org_id=uuid.uuid4(),
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="mask",
        )
        updated = await service.update_policy(
            policy.id,
            action="block",
            is_locked=True,
        )
        assert updated is not None
        assert updated.action == "block"
        assert updated.is_locked is True

    async def test_delete_is_soft(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        policy = await service.create_policy(
            org_id=uuid.uuid4(),
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="mask",
        )
        assert await service.delete_policy(policy.id) is True
        assert await service.get_policy(policy.id) is None
        # Deleted policy is excluded from default list queries
        listed = await service.list_policies()
        assert not any(p.id == policy.id for p in listed)
        # Re-deleting returns False
        assert await service.delete_policy(policy.id) is False

    async def test_delete_missing_returns_false(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        assert await service.delete_policy(uuid.uuid4()) is False


class TestDLPCustomRegexCRUD:
    async def test_create_and_list(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        rec = await service.create_custom_regex(
            org_id=uuid.uuid4(),
            workspace_id=None,
            name="HECATE_API_KEY",
            pattern=r"HEC-[A-Z0-9]{16}",
            entity_type="HECATE_API_KEY",
        )
        assert rec.id is not None
        assert rec.name == "HECATE_API_KEY"
        listed = await service.list_custom_regex()
        assert len(listed) == 1

    async def test_delete_returns_true_then_false(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        rec = await service.create_custom_regex(
            org_id=uuid.uuid4(),
            workspace_id=None,
            name="X",
            pattern=r"X+",
            entity_type="X",
        )
        assert await service.delete_custom_regex(rec.id) is True
        assert await service.delete_custom_regex(rec.id) is False


class TestDLPDictionaryCRUD:
    async def test_create_and_list(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        rec = await service.create_dictionary(
            org_id=uuid.uuid4(),
            workspace_id=None,
            name="Project Codenames",
            entity_type="CODENAME",
            terms=["Apollo", "Artemis", "Zeus"],
            case_sensitive=True,
        )
        assert rec.id is not None
        assert rec.terms == ["Apollo", "Artemis", "Zeus"]
        assert rec.case_sensitive is True

        listed = await service.list_dictionaries()
        assert len(listed) == 1

    async def test_create_copies_terms_list(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        original = ["a", "b"]
        rec = await service.create_dictionary(
            org_id=uuid.uuid4(),
            workspace_id=None,
            name="D",
            entity_type="X",
            terms=original,
        )
        original.append("c")
        assert rec.terms == ["a", "b"]


class TestDLPBuildResolver:
    @pytest.mark.xfail(
        reason="SQLite Boolean isnot(False) doesn't match server_default='true' value; works in PostgreSQL",
        strict=True,
    )
    async def test_build_resolver_materials_rules(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        org_id = uuid.uuid4()
        await service.create_policy(
            org_id=org_id,
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="mask",
        )
        resolver = await service.build_resolver(org_id=org_id)
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.MASK

    async def test_build_resolver_skips_invalid_actions(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        # Manually insert a policy with a bogus action value
        bad = DLPPolicyModel(
            org_id=uuid.uuid4(),
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="bogus",
        )
        db_session.add(bad)
        await db_session.flush()

        resolver = await service.build_resolver()
        assert resolver.resolve("EMAIL", "llm_output") == DLPAction.ALLOW


class TestDLPDryRunScan:
    async def test_dry_run_with_no_policies_returns_allow(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        result = await service.dry_run_scan("any text here", "llm_output", org_id=uuid.uuid4())
        assert result.action == DLPAction.ALLOW
        assert result.text == "any text here"
        assert result.findings == []

    async def test_dry_run_with_no_registry_uses_empty(self, db_session: AsyncSession) -> None:
        service = DLPService(db_session)
        org_id = uuid.uuid4()
        await service.create_policy(
            org_id=org_id,
            workspace_id=None,
            agent_id=None,
            entity_type="EMAIL",
            direction="llm_output",
            action="block",
        )
        result = await service.dry_run_scan("any text", "llm_output", org_id=org_id)
        # No recognizers in the default registry, so no findings → ALLOW
        # (the BLOCK rule exists but there's nothing to apply it to).
        assert result.action == DLPAction.ALLOW


class TestDLPMetadataHelpers:
    def test_known_entity_types(self) -> None:
        types = known_entity_types()
        assert "EMAIL" in types
        assert "SSN" in types

    def test_supported_directions(self) -> None:
        dirs = supported_directions()
        assert "llm_input" in dirs
        assert "llm_output" in dirs
        assert "tool_output" in dirs
        assert "mcp_response" in dirs
