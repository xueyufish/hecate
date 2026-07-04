"""SCIM models — mappers between scim2-models and Hecate UserModel."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def to_scim_user(user: object, location_url: str) -> dict[str, Any]:
    """Convert a Hecate UserModel to a SCIM User representation.

    Args:
        user: UserModel instance.
        location_url: URL for the resource location header.

    Returns:
        SCIM User JSON dict.
    """
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "externalId": getattr(user, "external_id", None),
        "userName": user.email,
        "name": {
            "givenName": getattr(user, "given_name", None) or "",
            "familyName": getattr(user, "family_name", None) or "",
            "formatted": getattr(user, "display_name", None) or "",
        },
        "displayName": getattr(user, "display_name", None) or user.email,
        "emails": [
            {"value": user.email, "type": "work", "primary": True},
        ],
        "active": getattr(user, "active", True),
        "meta": {
            "resourceType": "User",
            "created": (
                user.created_at.isoformat()
                if hasattr(user, "created_at") and user.created_at
                else datetime.now(UTC).isoformat()
            ),
            "lastModified": (
                user.updated_at.isoformat()
                if hasattr(user, "updated_at") and user.updated_at
                else datetime.now(UTC).isoformat()
            ),
            "location": location_url,
        },
    }


def from_scim_user(scim_data: dict[str, Any]) -> dict[str, Any]:
    """Extract Hecate UserModel fields from a SCIM User dict.

    Args:
        scim_data: SCIM User JSON dict.

    Returns:
        Dict with UserModel-compatible fields.
    """
    name = scim_data.get("name", {})
    emails = scim_data.get("emails", [])
    email = emails[0]["value"] if emails else scim_data.get("userName", "")

    return {
        "email": email,
        "external_id": scim_data.get("externalId"),
        "display_name": scim_data.get("displayName") or name.get("formatted"),
        "given_name": name.get("givenName"),
        "family_name": name.get("familyName"),
        "active": scim_data.get("active", True),
    }


def to_scim_group(group_id: str, display_name: str, members: list[dict[str, str]], location_url: str) -> dict[str, Any]:
    """Convert group data to SCIM Group representation.

    Args:
        group_id: Group UUID.
        display_name: Group display name.
        members: List of member dicts with 'value' (user_id) and 'display'.
        location_url: URL for the resource location header.

    Returns:
        SCIM Group JSON dict.
    """
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "id": group_id,
        "displayName": display_name,
        "members": members,
        "meta": {
            "resourceType": "Group",
            "created": datetime.now(UTC).isoformat(),
            "lastModified": datetime.now(UTC).isoformat(),
            "location": location_url,
        },
    }


def make_list_response(resources: list[dict], total: int, start_index: int, count: int) -> dict[str, Any]:
    """Create a SCIM ListResponse.

    Args:
        resources: List of SCIM resource dicts.
        total: Total number of matching resources.
        start_index: 1-based start index.
        count: Items per page.

    Returns:
        SCIM ListResponse JSON dict.
    """
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total,
        "startIndex": start_index,
        "itemsPerPage": len(resources),
        "Resources": resources,
    }


def make_error(status_code: int, detail: str, scim_type: str | None = None) -> dict[str, Any]:
    """Create a SCIM error response.

    Args:
        status_code: HTTP status code.
        detail: Error detail message.
        scim_type: Optional SCIM error type.

    Returns:
        SCIM Error JSON dict.
    """
    error: dict[str, Any] = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(status_code),
        "detail": detail,
    }
    if scim_type:
        error["scimType"] = scim_type
    return error
