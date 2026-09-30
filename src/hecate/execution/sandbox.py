"""SandboxProvider - process-external environment contract (plan step3).

Independent from the execution contract and versioned separately under
``https://hecate.dev/contracts/sandbox/<version>/``. Environment creation and
command submission are idempotent on their IDs; a termination request is not a
destroyed instance; a lost connection is not a released resource. The Docker
reference implementation is wrapped behind this contract in plan step7 - this
module only fixes the seam.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from hecate.contracts.execution.capabilities import CapabilityLevel
from hecate.contracts.execution.sandbox import (
    CommandRecord,
    CommandRequest,
    CommandState,
    CreateEnvironmentRequest,
    SandboxCapabilities,
    SandboxFileRef,
    SandboxInfo,
    SandboxState,
)


class UnsupportedCapabilityError(Exception):
    """Raised when an optional sandbox ability is requested while unsupported."""

    def __init__(self, ability: str, sandbox_id: str | None = None) -> None:
        super().__init__(f"sandbox ability {ability!r} is unsupported by this provider")
        self.ability = ability
        self.sandbox_id = sandbox_id
        self.code = "unsupported"


@dataclass
class SandboxLease:
    """Bookkeeping for one environment's lease (provider-owned resource)."""

    leases_until: str = field(default_factory=lambda: "2026-09-30T10:00:00Z")


def _advance_lease(leases_until: str, seconds: int) -> str:
    moment = datetime.fromisoformat(leases_until.replace("Z", "+00:00"))
    return (moment + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


class SandboxProvider(ABC):
    """Process-external sandbox seam: create/query, command, files, terminate, lease."""

    @abstractmethod
    def describe_capabilities(self) -> SandboxCapabilities:
        """Declare optional abilities explicitly; absence is never inferred as support."""

    @abstractmethod
    def create_environment(self, request: CreateEnvironmentRequest) -> SandboxInfo:
        """Idempotent on ``request.idempotency_id``: replay returns the original environment."""

    @abstractmethod
    def get_environment(self, sandbox_id: str) -> SandboxInfo:
        """Return observed state; a termination request is NOT a destroyed instance."""

    @abstractmethod
    def submit_command(self, request: CommandRequest) -> CommandRecord:
        """Idempotent on ``request.command_id``; timeout maps to ``unknown``."""

    @abstractmethod
    def get_command(self, command_id: str) -> CommandRecord:
        """Reconcile one command by ID instead of re-running it."""

    @abstractmethod
    def upload_file(self, file_ref: SandboxFileRef, content: bytes) -> None:
        """Transfer content into the environment via a sandbox-relative reference."""

    @abstractmethod
    def download_file(self, file_ref: SandboxFileRef) -> bytes:
        """Read content out of the environment via a sandbox-relative reference."""

    @abstractmethod
    def terminate(self, sandbox_id: str) -> SandboxInfo:
        """Request termination; observed state only reaches TERMINATED on confirmation."""

    @abstractmethod
    def renew_lease(self, sandbox_id: str, seconds: int) -> SandboxInfo:
        """Extend the environment lease; expiry policy is the provider's to enforce."""

    def _require_declared(self, ability: str, sandbox_id: str | None = None) -> None:
        if self.describe_capabilities().level_of(ability) is not CapabilityLevel.UNSUPPORTED:
            return
        raise UnsupportedCapabilityError(ability, sandbox_id)

    def snapshot(self, sandbox_id: str) -> dict[str, Any]:
        """Optional ability; default refuses undeclared providers explicitly."""

        self._require_declared("snapshot", sandbox_id)
        raise NotImplementedError("snapshot semantics are provider-defined (0.x draft)")


class InMemorySandboxProvider(SandboxProvider):
    """Test double for the sandbox contract (scenario-pack stub conventions).

    ``command_mode`` injects the timeout path deterministically: ``timeout``
    submits land in ``unknown`` until ``settle_command`` reveals the truth.
    """

    def __init__(self, command_mode: str = "ok") -> None:
        self.command_mode = command_mode
        self._environments: dict[str, SandboxInfo] = {}
        self._idempotency: dict[str, str] = {}
        self._commands: dict[str, CommandRecord] = {}
        self._command_runs: dict[str, int] = {}
        self._files: dict[tuple[str, str], bytes] = {}
        self._leases: dict[str, SandboxLease] = {}
        self._counter = 0

    def describe_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            pause=CapabilityLevel.UNSUPPORTED,
            resume=CapabilityLevel.UNSUPPORTED,
            snapshot=CapabilityLevel.UNSUPPORTED,
            fork=CapabilityLevel.UNSUPPORTED,
            browser=CapabilityLevel.UNSUPPORTED,
            gpu=CapabilityLevel.UNSUPPORTED,
        )

    def create_environment(self, request: CreateEnvironmentRequest) -> SandboxInfo:
        existing = self._idempotency.get(request.idempotency_id)
        if existing is not None:
            return self._environments[existing]

        self._counter += 1
        sandbox_id = f"sbx-{self._counter}"
        info = SandboxInfo(sandbox_id=sandbox_id, run_ref=request.run_ref, state=SandboxState.READY)
        self._environments[sandbox_id] = info
        self._idempotency[request.idempotency_id] = sandbox_id
        self._leases[sandbox_id] = SandboxLease()
        return info

    def get_environment(self, sandbox_id: str) -> SandboxInfo:
        return self._environments[sandbox_id]

    def submit_command(self, request: CommandRequest) -> CommandRecord:
        existing = self._commands.get(request.command_id)
        if existing is not None:
            return existing

        self._command_runs[request.command_id] = self._command_runs.get(request.command_id, 0) + 1
        if self.command_mode == "timeout":
            record = CommandRecord(
                command_id=request.command_id,
                sandbox_id=request.sandbox_id,
                state=CommandState.UNKNOWN,
                submitted_at="2026-09-30T10:06:00Z",
            )
        else:
            record = CommandRecord(
                command_id=request.command_id,
                sandbox_id=request.sandbox_id,
                state=CommandState.COMPLETED,
                exit_code=0,
                stdout_ref=f"mem://{request.command_id}/stdout",
                submitted_at="2026-09-30T10:05:00Z",
            )
        self._commands[request.command_id] = record
        return record

    def get_command(self, command_id: str) -> CommandRecord:
        return self._commands[command_id]

    def upload_file(self, file_ref: SandboxFileRef, content: bytes) -> None:
        self._files[(file_ref.sandbox_id, file_ref.path)] = content

    def download_file(self, file_ref: SandboxFileRef) -> bytes:
        return self._files[(file_ref.sandbox_id, file_ref.path)]

    def terminate(self, sandbox_id: str) -> SandboxInfo:
        prior = self._environments[sandbox_id]
        terminating = SandboxInfo(
            sandbox_id=prior.sandbox_id,
            run_ref=prior.run_ref,
            state=SandboxState.TERMINATING,
            leases_until=prior.leases_until,
        )
        self._environments[sandbox_id] = terminating
        return terminating

    def renew_lease(self, sandbox_id: str, seconds: int) -> SandboxInfo:
        lease = self._leases[sandbox_id]
        lease.leases_until = _advance_lease(lease.leases_until, seconds)
        prior = self._environments[sandbox_id]
        renewed = SandboxInfo(
            sandbox_id=prior.sandbox_id,
            run_ref=prior.run_ref,
            state=prior.state,
            leases_until=lease.leases_until,
        )
        self._environments[sandbox_id] = renewed
        return renewed

    # --- test helpers (not part of the contract surface) ---

    def environment_count(self) -> int:
        return len(self._environments)

    def command_execution_count(self, command_id: str) -> int:
        return self._command_runs.get(command_id, 0)

    def settle_command(self, command_id: str, exit_code: int) -> None:
        record = self._commands[command_id]
        self._commands[command_id] = CommandRecord(
            command_id=record.command_id,
            sandbox_id=record.sandbox_id,
            state=CommandState.COMPLETED,
            exit_code=exit_code,
            stdout_ref=f"mem://{command_id}/stdout",
            submitted_at=record.submitted_at,
        )

    def settle_termination(self, sandbox_id: str) -> SandboxInfo:
        prior = self._environments[sandbox_id]
        terminated = SandboxInfo(
            sandbox_id=prior.sandbox_id,
            run_ref=prior.run_ref,
            state=SandboxState.TERMINATED,
            leases_until=prior.leases_until,
        )
        self._environments[sandbox_id] = terminated
        return terminated
