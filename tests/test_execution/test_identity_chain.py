"""IdentityChain contract tests: four identity slots, validation, round-trip."""

from __future__ import annotations

import pytest

from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import RefKind, deployment_ref, run_ref


def _workload() -> WorkloadIdentity:
    return WorkloadIdentity(deployment=deployment_ref("hecate", "dep-1"), workload_id="wf-agent-7")


def test_chain_round_trips_through_dict() -> None:
    chain = IdentityChain(
        initiator="user-42",
        principal_id="principal-9",
        workload=_workload(),
        on_behalf_of=run_ref("hecate", "run-3"),
    )
    restored = IdentityChain.from_dict(chain.to_dict())
    assert restored == chain
    assert restored.workload.deployment.kind is RefKind.DEPLOYMENT


def test_system_initiated_chain_allows_none_initiator() -> None:
    chain = IdentityChain(initiator=None, principal_id="principal-9", workload=_workload())
    assert IdentityChain.from_dict(chain.to_dict()) == chain


def test_workload_rejects_wrong_ref_kind() -> None:
    with pytest.raises(ValueError, match="reference kind mismatch"):
        WorkloadIdentity(deployment=run_ref("hecate", "not-a-deployment"), workload_id="wf")


def test_chain_rejects_blank_principal_and_workload() -> None:
    with pytest.raises(ValueError, match="principal_id"):
        IdentityChain(initiator="user-1", principal_id="", workload=_workload())
    with pytest.raises(ValueError, match="workload_id"):
        WorkloadIdentity(deployment=deployment_ref("hecate", "d"), workload_id="")


def test_chain_rejects_blank_initiator_string() -> None:
    with pytest.raises(ValueError, match="initiator"):
        IdentityChain(initiator="", principal_id="p", workload=_workload())
