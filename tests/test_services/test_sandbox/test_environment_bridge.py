"""Tests for environment-to-sandbox volume bridge.

Covers resolve_environment_volumes() for DockerEnvironment,
LocalEnvironment, and None cases.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from hecate.services.sandbox.environment_bridge import (
    _SANDBOX_MOUNT_POINT,
    resolve_environment_volumes,
)


class TestResolveEnvironmentVolumes:
    def test_none_returns_empty(self) -> None:
        assert resolve_environment_volumes(None) == {}

    def test_docker_environment_resolves_to_named_volume(self) -> None:
        from hecate.services.environment.docker_environment import DockerEnvironment

        env = MagicMock(spec=DockerEnvironment)
        env._volume_name = "agent-test-123"
        result = resolve_environment_volumes(env)
        assert result == {"agent-test-123": _SANDBOX_MOUNT_POINT}

    def test_local_environment_resolves_to_host_path(self, tmp_path: Any) -> None:
        from hecate.services.environment.environment import LocalEnvironment

        env = MagicMock(spec=LocalEnvironment)
        env.root_path = tmp_path / "agent-test"
        result = resolve_environment_volumes(env)
        assert result == {str(env.root_path): _SANDBOX_MOUNT_POINT}

    def test_unknown_environment_returns_empty(self) -> None:
        env = MagicMock()
        result = resolve_environment_volumes(env)
        assert result == {}

    def test_mount_point_is_mnt_env(self) -> None:
        assert _SANDBOX_MOUNT_POINT == "/mnt/env"
