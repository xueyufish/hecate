"""Account-constrained REST writes for opted-in platform Controller tasks."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.gateway_target import GatewayTargetKind, GatewayTargetModel
from hecate.models.tool import ToolModel
from hecate.tools.gateway.errors import GatewayError
from hecate.tools.gateway.executor import RestToolExecutor, gateway_meta
from hecate.tools.gateway.targets import validate_base_url

REST_WRITE_VERSION = 1
REST_WRITE_REFUSED = "rest_write_refused"
_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_ACCOUNT = re.compile(r"[\w][\w.-]*")


class RestWriteDeclarationError(ValueError):
    """The host declaration is malformed or unresolvable."""


class RestWriteRefusedError(RuntimeError):
    """The executor refused dispatch before sending any request."""

    def __init__(self, verdict: dict[str, Any]) -> None:
        super().__init__(verdict["reason"])
        self.verdict = verdict


@dataclass(frozen=True)
class RestWriteProfile:
    """The immutable binding of one POST operation and its safe inputs."""

    tool: str
    target: str
    target_id: uuid.UUID
    base_url: str
    path: str
    account_param: str
    allowed_accounts: tuple[str, ...]
    body_params: tuple[str, ...]


def parse_rest_write_declaration(value: Any) -> RestWriteProfile:
    """Read the host-owned binding from an immutable agent version."""
    keys = {
        "version",
        "tool",
        "target",
        "target_id",
        "base_url",
        "path",
        "account_param",
        "allowed_accounts",
        "body_params",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise RestWriteDeclarationError("rest_write must declare exactly the target, operation and argument binding")
    if type(value["version"]) is not int or value["version"] != REST_WRITE_VERSION:
        raise RestWriteDeclarationError(f"rest_write.version must be {REST_WRITE_VERSION}")
    for name in ("tool", "target", "account_param"):
        if not isinstance(value[name], str) or not _NAME.fullmatch(value[name]):
            raise RestWriteDeclarationError(f"rest_write.{name} must be a lowercase identifier")
    try:
        target_id = uuid.UUID(value["target_id"])
    except (ValueError, AttributeError, TypeError) as exc:
        raise RestWriteDeclarationError("rest_write.target_id must be a target UUID") from exc

    base_url = value["base_url"]
    try:
        if not isinstance(base_url, str) or re.search(r"[\x00-\x20\x7f]", base_url):
            raise ValueError
        parsed = urlsplit(base_url)
        validate_base_url(base_url)
        _ = parsed.port  # Validate an explicit port using urlsplit's bounds check.
        if parsed.username is not None or parsed.query or parsed.fragment:
            raise ValueError
    except (GatewayError, ValueError) as exc:
        raise RestWriteDeclarationError("rest_write.base_url must be an HTTP URL without credentials") from exc

    param = value["account_param"]
    placeholder = "{" + param + "}"
    path = value["path"]
    if not isinstance(path, str) or not path.startswith("/") or path.split("/").count(placeholder) != 1:
        raise RestWriteDeclarationError("rest_write.path must contain the account placeholder as one path segment")
    accounts = value["allowed_accounts"]
    if (
        not isinstance(accounts, list)
        or not accounts
        or not all(isinstance(account, str) and _ACCOUNT.fullmatch(account) for account in accounts)
        or len(set(accounts)) != len(accounts)
    ):
        raise RestWriteDeclarationError("rest_write.allowed_accounts must contain unique account identifiers")
    body_params = value["body_params"]
    if (
        not isinstance(body_params, list)
        or not all(isinstance(name, str) and name and name != param for name in body_params)
        or len(set(body_params)) != len(body_params)
    ):
        raise RestWriteDeclarationError("rest_write.body_params must contain unique non-routing field names")
    return RestWriteProfile(
        tool=value["tool"],
        target=value["target"],
        target_id=target_id,
        base_url=base_url,
        path=path,
        account_param=param,
        allowed_accounts=tuple(accounts),
        body_params=tuple(body_params),
    )


class RestWriteExecutor(RestToolExecutor):
    """Apply an account binding to the exact registration used for each POST."""

    def __init__(self, profile: RestWriteProfile, *, workspace_id: uuid.UUID) -> None:
        super().__init__(trust_env=False)
        self._profile = profile
        self._workspace_id = workspace_id

    def _refusal(self, name: str, reason: str) -> dict[str, Any]:
        return {
            "status": REST_WRITE_REFUSED,
            "version": REST_WRITE_VERSION,
            "tool": name,
            "profile": {
                "tool": self._profile.tool,
                "target": self._profile.target,
                "account_param": self._profile.account_param,
            },
            "reason": reason,
        }

    def check_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Recognize a known no-effect refusal even for an invalid input shape."""
        profile = self._profile
        if name != profile.tool:
            return None
        account = arguments.get(profile.account_param)
        if not isinstance(account, str) or not account.strip() or account.strip() != account:
            return self._refusal(name, "account_missing")
        if account not in profile.allowed_accounts:
            return self._refusal(name, "account_denied")
        # The approved body fields carry scalar business data. Extra arguments
        # would otherwise become query parameters in the shared REST executor.
        if any(
            key != profile.account_param and (key not in profile.body_params or isinstance(value, (dict, list)))
            for key, value in arguments.items()
        ):
            return self._refusal(name, "account_override_attempt")
        return None

    def _binding_error(self, tool: ToolModel, target: GatewayTargetModel) -> str | None:
        profile = self._profile
        if tool.name != profile.tool or tool.source != "rest":
            return "tool_not_admitted"
        if tool.workspace_id != self._workspace_id or target.workspace_id != self._workspace_id:
            return "cross_workspace"
        if tool.deleted or target.deleted or not target.is_active or target.kind != GatewayTargetKind.REST:
            return "target_unavailable"
        if (
            tool.target_id != profile.target_id
            or target.id != profile.target_id
            or target.name != profile.target
            or target.base_url != profile.base_url
        ):
            return "target_rebound"
        try:
            meta = gateway_meta(tool)
        except GatewayError:
            return "operation_not_admitted"
        if meta["method"] != "POST" or meta["path"] != profile.path:
            return "operation_not_admitted"
        if (
            meta["path_params"] != [profile.account_param]
            or meta.get("query_params")
            or sorted(meta.get("body_params", [])) != sorted(profile.body_params)
        ):
            return "ambiguous_account_source"
        return None

    async def validate_setup(self, db: AsyncSession) -> None:
        """Refuse an unresolved or changed binding before the first model call."""
        row = (
            await db.execute(
                select(ToolModel, GatewayTargetModel)
                .join(GatewayTargetModel, GatewayTargetModel.id == ToolModel.target_id)
                .where(
                    ToolModel.name == self._profile.tool,
                    ToolModel.workspace_id == self._workspace_id,
                    ~ToolModel.deleted,
                )
            )
        ).one_or_none()
        reason = "tool_unavailable" if row is None else self._binding_error(*row)
        if reason is not None:
            raise RestWriteDeclarationError(f"rest_write binding is unavailable ({reason})")

    async def execute(
        self,
        tool: ToolModel,
        target: GatewayTargetModel,
        arguments: dict[str, Any],
        identity_headers: dict[str, str] | None = None,
    ) -> Any:
        """Send the approved POST or raise a no-effect refusal before send."""
        reason = self._binding_error(tool, target)
        if reason is not None:
            raise RestWriteRefusedError(self._refusal(tool.name, reason))
        blocked = self.check_arguments(tool.name, arguments)
        if blocked is not None:
            raise RestWriteRefusedError(blocked)
        return await super().execute(tool, target, arguments, identity_headers)
