"""Backend capability declaration: three-level semantics on three ownership axes.

Levels: ``unsupported`` (requesting it yields a structured UNSUPPORTED error),
``cooperative`` (backend participates when asked), ``enforced``
(platform-verified control). Ownership is declared on three independent axes -
harness owner, environment owner, tool/data-access execution point - never
collapsed into a single hosting label. Every non-unsupported capability MUST
carry a verification entry; absent or blank verification counts as missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CapabilityLevel(StrEnum):
    """Verifiable control level for one optional capability."""

    UNSUPPORTED = "unsupported"
    COOPERATIVE = "cooperative"
    ENFORCED = "enforced"


class HarnessOwner(StrEnum):
    """Who owns the agent harness (reasoning loop)."""

    HECATE = "hecate"
    ENTERPRISE = "enterprise"
    VENDOR = "vendor"


class EnvironmentOwner(StrEnum):
    """Who owns the sandbox/file-and-command environment (``none`` if unused)."""

    HECATE = "hecate"
    ENTERPRISE = "enterprise"
    VENDOR = "vendor"
    NONE = "none"


class ToolExecutionPoint(StrEnum):
    """Where tool and data-access calls actually execute."""

    HECATE_GATEWAY = "hecate_gateway"
    BACKEND = "backend"
    VENDOR_INTERNAL = "vendor_internal"
    MIXED = "mixed"


@dataclass(frozen=True)
class OwnershipAxes:
    """Three-axis ownership declaration (never one hosting label)."""

    harness: HarnessOwner
    environment: EnvironmentOwner
    tool_execution: ToolExecutionPoint

    def to_dict(self) -> dict[str, str]:
        return {
            "harness": self.harness.value,
            "environment": self.environment.value,
            "tool_execution": self.tool_execution.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OwnershipAxes:
        return cls(
            harness=HarnessOwner(data["harness"]),
            environment=EnvironmentOwner(data["environment"]),
            tool_execution=ToolExecutionPoint(data["tool_execution"]),
        )


@dataclass(frozen=True)
class CapabilityVerification:
    """Provenance for a declared capability; blanks count as missing."""

    source: str
    checked_at: str
    contract_version: str | None = None
    valid_until: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"source": self.source, "checked_at": self.checked_at}
        if self.contract_version is not None:
            out["contract_version"] = self.contract_version
        if self.valid_until is not None:
            out["valid_until"] = self.valid_until
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityVerification:
        return cls(
            source=data["source"],
            checked_at=data["checked_at"],
            contract_version=data.get("contract_version"),
            valid_until=data.get("valid_until"),
        )


@dataclass(frozen=True)
class CapabilitySet:
    """The five optional capabilities, each at an explicit level."""

    provide_input: CapabilityLevel
    resolve_approval: CapabilityLevel
    pause: CapabilityLevel
    resume: CapabilityLevel
    export_context: CapabilityLevel

    def level_of(self, name: str) -> CapabilityLevel:
        return getattr(self, name)

    def to_dict(self) -> dict[str, str]:
        return {
            "provide_input": self.provide_input.value,
            "resolve_approval": self.resolve_approval.value,
            "pause": self.pause.value,
            "resume": self.resume.value,
            "export_context": self.export_context.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilitySet:
        return cls(
            provide_input=CapabilityLevel(data["provide_input"]),
            resolve_approval=CapabilityLevel(data["resolve_approval"]),
            pause=CapabilityLevel(data["pause"]),
            resume=CapabilityLevel(data["resume"]),
            export_context=CapabilityLevel(data["export_context"]),
        )


@dataclass(frozen=True)
class BackendCapabilities:
    """Full capability declaration for one execution backend."""

    contract_version: str
    backend_type: str
    ownership: OwnershipAxes
    capabilities: CapabilitySet
    verification: dict[str, CapabilityVerification] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.contract_version or not self.backend_type:
            raise ValueError("contract_version and backend_type must be non-empty")
        for name in ("provide_input", "resolve_approval", "pause", "resume", "export_context"):
            if self.capabilities.level_of(name) is not CapabilityLevel.UNSUPPORTED:
                entry = self.verification.get(name)
                if entry is None or not entry.source or not entry.checked_at:
                    raise ValueError(
                        f"capability {name!r} is {self.capabilities.level_of(name).value} "
                        "but has no verification entry (source/checked_at are required)"
                    )

    def level_of(self, name: str) -> CapabilityLevel:
        return self.capabilities.level_of(name)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "contract_version": self.contract_version,
            "backend_type": self.backend_type,
            "ownership": self.ownership.to_dict(),
            "capabilities": self.capabilities.to_dict(),
        }
        if self.verification:
            out["verification"] = {name: entry.to_dict() for name, entry in self.verification.items()}
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackendCapabilities:
        known = {"contract_version", "backend_type", "ownership", "capabilities", "verification"}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(
            contract_version=data["contract_version"],
            backend_type=data["backend_type"],
            ownership=OwnershipAxes.from_dict(data["ownership"]),
            capabilities=CapabilitySet.from_dict(data["capabilities"]),
            verification={
                name: CapabilityVerification.from_dict(entry) for name, entry in data.get("verification", {}).items()
            },
            extra=extra,
        )
