"""Controlled action record: the governance shape every dispatch path shares.

A controlled action binds one logical tool call to its verified identity
envelope, arguments digest, target resources, side-effect class, policy
decision, approval reference and execution receipt. Error categories are
distinct so callers downstream (reconciliation, dashboards, gates) can tell a
business rejection from a technical failure, an unknown outcome or a policy
denial — an unknown outcome never retries blindly and never masquerades as
success.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hecate_governance.principal import ActionPrincipal


class ActionSideEffectClass(StrEnum):
    """Retry/recovery semantics for one tool.

    Values mirror ``hecate_durable.contracts.tools.ToolSideEffectClass`` and
    ``runtime.tool_side_effects.SideEffectClass``. Duplicated here as pure data
    so this package installs without the durable storage dependency; a
    platform-side test pins the vocabularies identical.
    """

    READONLY = "readonly"
    IDEMPOTENT_WRITE = "idempotent_write"
    NON_IDEMPOTENT_WRITE = "non_idempotent_write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    UNKNOWN = "unknown"


class ActionDecision(StrEnum):
    """Outcome of policy evaluation for one action."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ActionErrorCategory(StrEnum):
    """Distinct failure classes recorded on the receipt."""

    SCHEMA_INVALID = "schema_invalid"
    BUSINESS_REJECTED = "business_rejected"
    TECHNICAL_FAILURE = "technical_failure"
    OUTCOME_UNKNOWN = "outcome_unknown"
    POLICY_DENIED = "policy_denied"


class ReceiptStatus(StrEnum):
    """Execution receipt lifecycle for one action."""

    CLAIMED = "claimed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


def tool_arguments_digest(arguments: Mapping[str, Any]) -> str:
    """Canonical SHA-256 over the JSON encoding of tool arguments.

    Mirrors the digest used by the durable action ledger so approval bindings
    and ledger records speak the same identity language.
    """
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=repr)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def target_resources_for(arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """Derive stable resource bindings from the validated dispatch arguments."""
    return tuple(
        sorted(f"{key}:{arguments[key]}" for key in ("domain", "sku", "path", "target_resource") if key in arguments)
    )


class ActionReceipt(BaseModel):
    """Execution receipt of one controlled action."""

    model_config = ConfigDict(frozen=True)

    status: ReceiptStatus
    result_digest: str | None = None
    error_category: ActionErrorCategory | None = None
    error_detail: str | None = None


class ControlledAction(BaseModel):
    """One controlled action as recorded by the governance service."""

    model_config = ConfigDict(frozen=True)

    invocation_id: str
    tool_name: str
    arguments_digest: str
    target_resources: tuple[str, ...] = Field(default=())
    side_effect_class: ActionSideEffectClass
    principal: ActionPrincipal
    decision: ActionDecision | None = None
    policy_version: str | None = None
    approval_ref: str | None = None
    compensates_action_id: str | None = None
    receipt: ActionReceipt | None = None
