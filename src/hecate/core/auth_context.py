"""Authentication context for workspace-aware request processing.

Provides the AuthContext dataclass that carries the full resolved
authentication state (user, organization, workspace, role, method)
for downstream dependency injection and authorization checks.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Literal

from hecate.models.workspace_member import WorkspaceRole


@dataclasses.dataclass(frozen=True)
class AuthContext:
    """Resolved authentication context for a single request.

    Carries the full identity and authorization state extracted from
    either a JWT bearer token or a database-backed API key. Downstream
    code uses this instead of raw token strings or user IDs.

    Attributes:
        user_id: The authenticated user's UUID.
        org_id: The active organization UUID (None for system-scope keys).
        workspace_id: The active workspace UUID (None for system-scope keys).
        role: The user's role in the active workspace (None for system-scope keys).
        auth_method: How the request was authenticated ("jwt", "api_key", "sso", or "ldap").
        api_key_scope: The API key's scope (None for JWT auth).
    """

    user_id: uuid.UUID
    org_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    role: WorkspaceRole | None
    auth_method: Literal["jwt", "api_key", "sso", "ldap"]
    api_key_scope: Literal["system", "workspace"] | None

    @property
    def is_system_scope(self) -> bool:
        """Check if this context has system-level access."""
        return self.api_key_scope == "system" or self.workspace_id is None

    @property
    def is_workspace_member(self) -> bool:
        """Check if this context has a workspace membership (role assigned)."""
        return self.role is not None and self.workspace_id is not None
