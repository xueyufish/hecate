"""Sandbox-provider contract DTOs (independent of the execution contract).

Environment creation and command submission accept idempotent IDs: replaying
the same ID returns the original resource, never a second instance. File
transfer uses sandbox-relative path references, never host paths or container
objects. Versioned independently under
``https://hecate.dev/contracts/sandbox/<version>/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from hecate.contracts.execution.capabilities import CapabilityLevel
from hecate.contracts.execution.references import BackendRef, RefKind, require_kind


class SandboxState(StrEnum):
    """Lifecycle state of one sandbox environment."""

    CREATING = "creating"
    READY = "ready"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    FAILED = "failed"
    UNKNOWN = "unknown"


class CommandState(StrEnum):
    """Execution state of one submitted command."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


SANDBOX_ABILITY_NAMES = ("pause", "resume", "snapshot", "fork", "browser", "gpu")


@dataclass(frozen=True)
class SandboxCapabilities:
    """Optional abilities, each declared explicitly - never inferred."""

    pause: CapabilityLevel
    resume: CapabilityLevel
    snapshot: CapabilityLevel
    fork: CapabilityLevel
    browser: CapabilityLevel
    gpu: CapabilityLevel

    def level_of(self, name: str) -> CapabilityLevel:
        return getattr(self, name)

    def to_dict(self) -> dict[str, str]:
        return {name: self.level_of(name).value for name in SANDBOX_ABILITY_NAMES}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxCapabilities:
        missing = [name for name in SANDBOX_ABILITY_NAMES if name not in data]
        if missing:
            raise ValueError(f"sandbox capabilities missing: {missing}")
        return cls(
            pause=CapabilityLevel(data["pause"]),
            resume=CapabilityLevel(data["resume"]),
            snapshot=CapabilityLevel(data["snapshot"]),
            fork=CapabilityLevel(data["fork"]),
            browser=CapabilityLevel(data["browser"]),
            gpu=CapabilityLevel(data["gpu"]),
        )


@dataclass(frozen=True)
class CreateEnvironmentRequest:
    """Create-one-environment request with an idempotent ID."""

    idempotency_id: str
    run_ref: BackendRef
    isolation_profile: str | None = None
    egress_policy_ref: str | None = None
    resource_limits: dict[str, Any] = field(default_factory=dict)
    backend_config_ns: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.run_ref, RefKind.RUN)
        if not self.idempotency_id:
            raise ValueError("idempotency_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"idempotency_id": self.idempotency_id, "run_ref": self.run_ref.to_dict()}
        if self.isolation_profile is not None:
            out["isolation_profile"] = self.isolation_profile
        if self.egress_policy_ref is not None:
            out["egress_policy_ref"] = self.egress_policy_ref
        if self.resource_limits:
            out["resource_limits"] = self.resource_limits
        if self.backend_config_ns:
            out["backend_config_ns"] = self.backend_config_ns
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreateEnvironmentRequest:
        known = {
            "idempotency_id",
            "run_ref",
            "isolation_profile",
            "egress_policy_ref",
            "resource_limits",
            "backend_config_ns",
        }
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            idempotency_id=data["idempotency_id"],
            run_ref=BackendRef.from_dict(data["run_ref"]),
            isolation_profile=data.get("isolation_profile"),
            egress_policy_ref=data.get("egress_policy_ref"),
            resource_limits=data.get("resource_limits", {}),
            backend_config_ns=data.get("backend_config_ns", {}),
            extra=extra,
        )


@dataclass(frozen=True)
class SandboxInfo:
    """Observed state of one sandbox environment."""

    sandbox_id: str
    run_ref: BackendRef
    state: SandboxState
    leases_until: str | None = None
    detail_ns: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_kind(self.run_ref, RefKind.RUN)
        if not self.sandbox_id:
            raise ValueError("sandbox_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "sandbox_id": self.sandbox_id,
            "run_ref": self.run_ref.to_dict(),
            "state": self.state.value,
        }
        if self.leases_until is not None:
            out["leases_until"] = self.leases_until
        if self.detail_ns:
            out["detail_ns"] = self.detail_ns
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxInfo:
        known = {"sandbox_id", "run_ref", "state", "leases_until", "detail_ns"}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            sandbox_id=data["sandbox_id"],
            run_ref=BackendRef.from_dict(data["run_ref"]),
            state=SandboxState(data["state"]),
            leases_until=data.get("leases_until"),
            detail_ns=data.get("detail_ns", {}),
            extra=extra,
        )


@dataclass(frozen=True)
class CommandRequest:
    """Submit-one-command request with an idempotent command ID."""

    command_id: str
    sandbox_id: str
    argv: tuple[str, ...]
    timeout_seconds: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command_id:
            raise ValueError("command_id must be a non-empty string")
        if not self.sandbox_id:
            raise ValueError("sandbox_id must be a non-empty string")
        if not self.argv:
            raise ValueError("argv must be a non-empty tuple of strings")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "command_id": self.command_id,
            "sandbox_id": self.sandbox_id,
            "argv": list(self.argv),
        }
        if self.timeout_seconds is not None:
            out["timeout_seconds"] = self.timeout_seconds
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandRequest:
        known = {"command_id", "sandbox_id", "argv", "timeout_seconds"}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            command_id=data["command_id"],
            sandbox_id=data["sandbox_id"],
            argv=tuple(data["argv"]),
            timeout_seconds=data.get("timeout_seconds"),
            extra=extra,
        )


@dataclass(frozen=True)
class CommandRecord:
    """Observed state of one submitted command (timeout => unknown)."""

    command_id: str
    sandbox_id: str
    state: CommandState
    exit_code: int | None = None
    stdout_ref: str | None = None
    stderr_ref: str | None = None
    submitted_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command_id:
            raise ValueError("command_id must be a non-empty string")
        if not self.sandbox_id:
            raise ValueError("sandbox_id must be a non-empty string")
        if (
            self.state is not CommandState.UNKNOWN
            and self.exit_code is None
            and self.state
            in (
                CommandState.COMPLETED,
                CommandState.FAILED,
            )
        ):
            raise ValueError(f"state {self.state.value} requires an exit_code")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "command_id": self.command_id,
            "sandbox_id": self.sandbox_id,
            "state": self.state.value,
        }
        if self.exit_code is not None:
            out["exit_code"] = self.exit_code
        if self.stdout_ref is not None:
            out["stdout_ref"] = self.stdout_ref
        if self.stderr_ref is not None:
            out["stderr_ref"] = self.stderr_ref
        if self.submitted_at is not None:
            out["submitted_at"] = self.submitted_at
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandRecord:
        known = {
            "command_id",
            "sandbox_id",
            "state",
            "exit_code",
            "stdout_ref",
            "stderr_ref",
            "submitted_at",
        }
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            command_id=data["command_id"],
            sandbox_id=data["sandbox_id"],
            state=CommandState(data["state"]),
            exit_code=data.get("exit_code"),
            stdout_ref=data.get("stdout_ref"),
            stderr_ref=data.get("stderr_ref"),
            submitted_at=data.get("submitted_at"),
            extra=extra,
        )


@dataclass(frozen=True)
class SandboxFileRef:
    """Reference to a file inside one sandbox (never a host path)."""

    sandbox_id: str
    path: str

    def __post_init__(self) -> None:
        if not self.sandbox_id or not self.path:
            raise ValueError("sandbox_id and path must be non-empty strings")
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("path must be sandbox-relative without traversal segments")

    def to_dict(self) -> dict[str, str]:
        return {"sandbox_id": self.sandbox_id, "path": self.path}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SandboxFileRef:
        return cls(sandbox_id=data["sandbox_id"], path=data["path"])
