"""Agent Environment — unified agent execution environment abstraction."""

from __future__ import annotations

from hecate_sandbox.environment.docker_environment import DockerEnvironment
from hecate_sandbox.environment.environment import (
    AgentEnvironment,
    ExecResult,
    FileInfo,
    LocalEnvironment,
)
from hecate_sandbox.environment.manager import EnvironmentManager

__all__ = [
    "AgentEnvironment",
    "DockerEnvironment",
    "EnvironmentManager",
    "ExecResult",
    "FileInfo",
    "LocalEnvironment",
]
