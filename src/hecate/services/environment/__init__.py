"""Agent Environment — unified agent execution environment abstraction."""

from __future__ import annotations

from hecate.services.environment.docker_environment import DockerEnvironment
from hecate.services.environment.environment import (
    AgentEnvironment,
    ExecResult,
    FileInfo,
    LocalEnvironment,
)
from hecate.services.environment.manager import EnvironmentManager

__all__ = [
    "AgentEnvironment",
    "DockerEnvironment",
    "EnvironmentManager",
    "ExecResult",
    "FileInfo",
    "LocalEnvironment",
]
