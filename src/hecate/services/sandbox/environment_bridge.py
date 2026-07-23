"""Environment-to-sandbox volume bridge.

Resolves an AgentEnvironment into Docker volume mount mappings suitable
for SandboxConfig. Handles both DockerEnvironment (named Docker volume)
and LocalEnvironment (host bind mount) backends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hecate.services.environment.environment import AgentEnvironment

_SANDBOX_MOUNT_POINT = "/mnt/env"


def resolve_environment_volumes(env: AgentEnvironment | None) -> dict[str, str]:
    """Resolve an AgentEnvironment into a volume mount mapping.

    Args:
        env: The agent's environment, or None if no environment is available.

    Returns:
        A dict mapping host path (or Docker volume name) to container mount
        path. Empty dict when ``env`` is None.
    """
    if env is None:
        return {}

    from hecate.services.environment.docker_environment import DockerEnvironment
    from hecate.services.environment.environment import LocalEnvironment

    if isinstance(env, DockerEnvironment):
        return {env._volume_name: _SANDBOX_MOUNT_POINT}

    if isinstance(env, LocalEnvironment):
        return {str(env.root_path): _SANDBOX_MOUNT_POINT}

    return {}
