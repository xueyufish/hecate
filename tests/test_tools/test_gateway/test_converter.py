"""Tests for OpenAPI → gateway tool conversion."""

from __future__ import annotations

import pytest

from hecate.models.gateway_target import GatewayTargetCreateSchema, GatewayTargetKind
from hecate.models.tool import ToolModel
from hecate.tools.gateway.converter import parse_openapi_operations, project_target_tools
from hecate.tools.gateway.errors import SpecConversionError
from hecate.tools.gateway.targets import GatewayTargetService

SAMPLE_SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "CRM", "version": "1.0"},
    "paths": {
        "/contacts/{contact_id}": {
            "get": {
                "operationId": "getContact",
                "summary": "Fetch a contact",
                "parameters": [
                    {
                        "name": "contact_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {"name": "expand", "in": "query", "schema": {"type": "boolean"}},
                ],
            },
            "delete": {
                "operationId": "deleteContact",
                "parameters": [
                    {
                        "name": "contact_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            },
        },
        "/contacts": {
            "post": {
                "operationId": "createContact",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "email": {"type": "string"},
                                },
                                "required": ["name"],
                            }
                        }
                    }
                },
            }
        },
    },
}


def _make_target(workspace_id=None):
    from hecate.models.gateway_target import GatewayTargetModel

    return GatewayTargetModel(
        name="crm",
        kind=GatewayTargetKind.REST,
        base_url="https://api.example.com",
        spec=SAMPLE_SPEC,
        workspace_id=workspace_id,
    )


class TestParseOpenapi:
    def test_three_operations_parsed(self) -> None:
        operations, warnings = parse_openapi_operations(SAMPLE_SPEC)
        assert [op.name for op in operations] == ["getContact", "deleteContact", "createContact"]
        assert warnings == []

    def test_path_parameter_required_in_schema(self) -> None:
        operations, _ = parse_openapi_operations(SAMPLE_SPEC)
        get_op = operations[0]
        assert get_op.method == "GET"
        assert get_op.path == "/contacts/{contact_id}"
        assert get_op.parameters_schema["required"] == ["contact_id"]
        assert "expand" in get_op.parameters_schema["properties"]

    def test_request_body_inlined_into_schema(self) -> None:
        operations, _ = parse_openapi_operations(SAMPLE_SPEC)
        create_op = operations[2]
        props = create_op.parameters_schema["properties"]
        assert set(props) == {"name", "email"}
        assert create_op.parameters_schema["required"] == ["name"]

    def test_operation_id_derived_when_missing(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "paths": {"/contacts/{contact_id}/notes": {"get": {"summary": "List notes"}}},
        }
        operations, _ = parse_openapi_operations(spec)
        assert operations[0].name == "get-contacts-by-contact-id-notes"

    def test_malformed_rejected_not_dict(self) -> None:
        with pytest.raises(SpecConversionError):
            parse_openapi_operations(["not", "a", "dict"])

    def test_swagger_2_rejected(self) -> None:
        with pytest.raises(SpecConversionError, match="only 3.x"):
            parse_openapi_operations({"swagger": "2.0", "paths": {}})

    def test_missing_paths_rejected(self) -> None:
        with pytest.raises(SpecConversionError, match="no paths"):
            parse_openapi_operations({"openapi": "3.0.0"})

    def test_exotic_document_skips_with_warnings(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/hook": {
                    "get": {"operationId": "ok", "callbacks": {"done": {"x": {}}}},
                    "head": {"operationId": "nope"},
                    "servers": [{"url": "https://evil.example.com"}],
                }
            },
        }
        operations, warnings = parse_openapi_operations(spec)
        assert [op.name for op in operations] == ["ok"]
        assert any("callbacks" in w for w in warnings)
        assert any("unsupported method" in w for w in warnings)
        assert any("server overrides are ignored" in w for w in warnings)


class TestProjection:
    async def test_full_import_creates_tool_rows(self, db_session) -> None:
        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                spec=SAMPLE_SPEC,
            )
        )
        report = await project_target_tools(db_session, target)
        assert sorted(report.imported) == [
            "crm__createContact",
            "crm__deleteContact",
            "crm__getContact",
        ]

        from sqlalchemy import select

        rows = (
            (await db_session.execute(select(ToolModel).where(ToolModel.source == "rest", ~ToolModel.deleted)))
            .scalars()
            .all()
        )
        assert len(rows) == 3
        get_row = next(r for r in rows if r.name == "crm__getContact")
        assert get_row.target_id == target.id
        assert get_row.risk_level == "LOW"
        assert get_row.parameters["properties"]["contact_id"] == {"type": "string"}

    async def test_malformed_spec_creates_no_rows(self, db_session) -> None:
        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="bad",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                spec={"swagger": "2.0"},
            )
        )
        with pytest.raises(SpecConversionError):
            await project_target_tools(db_session, target)
        from sqlalchemy import select

        rows = (await db_session.execute(select(ToolModel).where(ToolModel.source == "rest"))).scalars().all()
        assert rows == []

    async def test_reimport_replaces_projection(self, db_session) -> None:
        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                spec=SAMPLE_SPEC,
            )
        )
        await project_target_tools(db_session, target)

        target.spec = {
            "openapi": "3.0.0",
            "paths": {"/ping": {"get": {"operationId": "ping"}}},
        }
        report = await project_target_tools(db_session, target)
        assert report.imported == ["crm__ping"]

        from sqlalchemy import select

        all_rows = (await db_session.execute(select(ToolModel).where(ToolModel.source == "rest"))).scalars().all()
        live = [r for r in all_rows if not r.deleted]
        old = [r for r in all_rows if r.deleted]
        assert [r.name for r in live] == ["crm__ping"]
        assert len(old) == 3
