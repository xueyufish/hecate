"""Tests for gateway target registration, egress baseline, and redaction."""

from __future__ import annotations

import pytest

from hecate.models.gateway_target import GatewayTargetCreateSchema, GatewayTargetKind
from hecate.tools.gateway.errors import (
    DuplicateTargetError,
    EgressPolicyError,
    TargetNotFoundError,
)
from hecate.tools.gateway.targets import (
    GatewayTargetService,
    redact_credentials,
    validate_base_url,
)


class TestEgressBaseline:
    def test_https_non_loopback_allowed(self) -> None:
        validate_base_url("https://api.example.com")

    def test_http_loopback_allowed(self) -> None:
        validate_base_url("http://localhost:9000")
        validate_base_url("http://127.0.0.1:9000")

    def test_http_non_loopback_rejected(self) -> None:
        with pytest.raises(EgressPolicyError, match="HTTPS is required"):
            validate_base_url("http://api.example.com")

    def test_unsupported_scheme_rejected(self) -> None:
        with pytest.raises(EgressPolicyError, match="Unsupported URL scheme"):
            validate_base_url("ftp://api.example.com")

    def test_missing_host_rejected(self) -> None:
        with pytest.raises(EgressPolicyError, match="no host"):
            validate_base_url("https://")


class TestRedactCredentials:
    def test_leaf_values_replaced_keys_preserved(self) -> None:
        creds = {"headers": {"Authorization": "Bearer secret", "X-Api-Key": "k123"}}
        redacted = redact_credentials(creds)
        assert redacted == {"headers": {"Authorization": "***", "X-Api-Key": "***"}}

    def test_original_untouched(self) -> None:
        creds = {"api_key": "secret"}
        redact_credentials(creds)
        assert creds == {"api_key": "secret"}


class TestTargetRegistration:
    async def test_create_rest_target(self, db_session) -> None:
        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                spec={"openapi": "3.0.0"},
                credentials={"headers": {"Authorization": "Bearer x"}},
            )
        )
        assert target.name == "crm"
        assert target.kind == GatewayTargetKind.REST
        assert target.is_active is True
        assert target.workspace_id is None

    async def test_duplicate_name_rejected(self, db_session) -> None:
        service = GatewayTargetService(db_session)
        payload = GatewayTargetCreateSchema(
            name="crm",
            kind=GatewayTargetKind.REST,
            base_url="https://api.example.com",
        )
        await service.create_target(payload)
        with pytest.raises(DuplicateTargetError, match="already exists"):
            await service.create_target(payload)

    async def test_same_name_different_workspace_allowed(self, db_session) -> None:
        import uuid

        service = GatewayTargetService(db_session)
        ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
        await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                workspace_id=ws_a,
            )
        )
        target_b = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                workspace_id=ws_b,
            )
        )
        assert target_b.workspace_id == ws_b

    async def test_http_non_loopback_registration_rejected(self, db_session) -> None:
        service = GatewayTargetService(db_session)
        with pytest.raises(EgressPolicyError):
            await service.create_target(
                GatewayTargetCreateSchema(
                    name="insecure",
                    kind=GatewayTargetKind.REST,
                    base_url="http://api.example.com",
                )
            )

    async def test_invalid_kind_rejected_by_schema(self, db_session) -> None:
        service = GatewayTargetService(db_session)
        with pytest.raises(ValueError, match="kind"):
            await service.create_target(
                GatewayTargetCreateSchema(
                    name="bad",
                    kind="grpc",  # type: ignore[arg-type]
                    base_url="https://api.example.com",
                )
            )

    async def test_get_missing_target_raises(self, db_session) -> None:
        import uuid

        service = GatewayTargetService(db_session)
        with pytest.raises(TargetNotFoundError):
            await service.get_target(uuid.uuid4())

    async def test_deactivate_soft_deletes(self, db_session) -> None:
        import uuid

        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
            )
        )
        await service.deactivate_target(target.id)
        with pytest.raises(TargetNotFoundError):
            await service.get_target(target.id)
        # name can be reused after deactivation
        recreated = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                workspace_id=uuid.uuid4(),
            )
        )
        assert recreated.id != target.id
