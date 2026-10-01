"""Security-claims mapping tests: parse, round-trip, and self-assertion rules.

Claim-set possession is not authentication - the schema and mapping define
claim names and constraints; verification semantics belong to plan step7.
"""

from __future__ import annotations

import pytest

from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.contracts.execution.request import ExecutionRequest
from hecate.contracts.execution.security import SecurityClaims
from tests.test_execution.conftest import SAMPLES_DIR, load_sample

CLAIMS_DIR = SAMPLES_DIR / "security-claims"


def test_valid_claim_set_round_trips() -> None:
    data = load_sample(CLAIMS_DIR / "valid-claim-set.json")
    claims = SecurityClaims.from_dict(data)
    assert claims.to_dict() == data
    assert claims.delegation_ref is not None
    assert claims.delegation_ref.kind is RefKind.AUTHORIZATION


def test_missing_aud_is_rejected_by_mapping() -> None:
    with pytest.raises((ValueError, KeyError)):
        SecurityClaims.from_dict(load_sample(CLAIMS_DIR / "negative-missing-aud.json"))


def test_aud_must_differ_from_iss() -> None:
    data = load_sample(CLAIMS_DIR / "valid-claim-set.json")
    with pytest.raises(ValueError, match="aud"):
        SecurityClaims.from_dict({**data, "aud": data["iss"]})


def test_unknown_claim_fields_survive_round_trip() -> None:
    data = load_sample(CLAIMS_DIR / "short-lived-claim-set.json")
    extended = {**data, "scope": "inventory:read", "jti": "c-1"}
    claims = SecurityClaims.from_dict(extended)
    assert claims.to_dict() == extended


def _submit_request_with_self_asserted_role() -> ExecutionRequest:
    sample = load_sample(SAMPLES_DIR / "http" / "self-asserted-role-ignored.json")
    return ExecutionRequest.from_dict(sample["request"]["body"])


def test_self_asserted_role_lands_only_in_extra() -> None:
    """A self-asserted role in the body never reaches an authorization field."""

    request = _submit_request_with_self_asserted_role()
    assert request.extra.get("role") == "admin"
    assert request.extra.get("is_platform_admin") is True
    for field in ("role", "is_platform_admin", "permissions", "admin"):
        assert not hasattr(request, field), field


def test_authorization_credential_is_reference_not_inline_secret() -> None:
    """The request carries an authorization REFERENCE; no secret fields exist."""

    request = _submit_request_with_self_asserted_role()
    assert request.authorization_ref.kind is RefKind.AUTHORIZATION
    assert not hasattr(request, "token") and not hasattr(request, "secret")
    assert isinstance(request.authorization_ref, BackendRef)
