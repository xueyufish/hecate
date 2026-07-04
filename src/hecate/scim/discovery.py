"""SCIM discovery endpoints — ServiceProviderConfig, Schemas, ResourceTypes."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/scim/v2", tags=["scim-discovery"])

SCIM_CONTENT_TYPE = "application/scim+json"


def _scim_response(content: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status_code, media_type=SCIM_CONTENT_TYPE)


@router.get("/ServiceProviderConfig")
async def get_service_provider_config() -> JSONResponse:
    """Return SCIM service provider configuration."""
    return _scim_response(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 1000},
            "changePassword": {"supported": False},
            "sort": {"supported": True},
            "etag": {"supported": True},
            "authenticationSchemes": [
                {
                    "name": "OAuth Bearer Token",
                    "description": "Authentication scheme using the OAuth Bearer Token Standard",
                    "specUri": "https://www.rfc-editor.org/info/rfc6750",
                    "type": "oauthbearertoken",
                    "primary": True,
                },
            ],
        }
    )


@router.get("/Schemas")
async def list_schemas() -> JSONResponse:
    """Return available SCIM schemas."""
    schemas = [
        {
            "id": "urn:ietf:params:scim:schemas:core:2.0:User",
            "name": "User",
            "description": "User Account",
            "attributes": [
                {"name": "userName", "type": "string", "required": True, "uniqueness": "server"},
                {
                    "name": "name",
                    "type": "complex",
                    "subAttributes": [
                        {"name": "givenName", "type": "string"},
                        {"name": "familyName", "type": "string"},
                        {"name": "formatted", "type": "string"},
                    ],
                },
                {"name": "displayName", "type": "string"},
                {"name": "emails", "type": "complex", "multiValued": True},
                {"name": "active", "type": "boolean"},
                {"name": "externalId", "type": "string"},
            ],
        },
        {
            "id": "urn:ietf:params:scim:schemas:core:2.0:Group",
            "name": "Group",
            "description": "Group",
            "attributes": [
                {"name": "displayName", "type": "string", "required": True},
                {"name": "members", "type": "complex", "multiValued": True},
            ],
        },
    ]

    return _scim_response(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(schemas),
            "startIndex": 1,
            "itemsPerPage": len(schemas),
            "Resources": schemas,
        }
    )


@router.get("/ResourceTypes")
async def list_resource_types() -> JSONResponse:
    """Return available resource types."""
    resource_types = [
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "User",
            "name": "User",
            "endpoint": "/scim/v2/Users",
            "description": "User Account",
            "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
        },
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
            "id": "Group",
            "name": "Group",
            "endpoint": "/scim/v2/Groups",
            "description": "Group",
            "schema": "urn:ietf:params:scim:schemas:core:2.0:Group",
        },
    ]

    return _scim_response(
        {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(resource_types),
            "startIndex": 1,
            "itemsPerPage": len(resource_types),
            "Resources": resource_types,
        }
    )
