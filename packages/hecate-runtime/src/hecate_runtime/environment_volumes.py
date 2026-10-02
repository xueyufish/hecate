"""Engine-side helper: resolve AgentEnvironment to sandbox volume mounts.

Pure data transform — no I/O, no port calls. Lives in runtime/ (not services/)
because tool_worker.py is the only consumer and engine/ must not import services/.

Duck-typed: only checks for ``_volume_name`` (DockerEnvironment) and
``root_path`` (LocalEnvironment) attributes. Avoids any ``isinstance`` against
concrete classes imported from ``services.environment`` so engine/ stays
dependency-free at the function-local level too (the PR0.1 layering invariant
test pins the default ``RuntimePort.tool_execute_sandbox``; this file is the
companion guarantee for the only sandbox path that actually executes).
"""

from __future__ import annotations

from typing import Any

_SANDBOX_MOUNT_POINT = "/mnt/env"


def resolve_environment_volumes(env: Any | None) -> dict[str, str]:
    """Resolve an agent environment into a sandbox volume-mount mapping.

    Args:
        env: The agent's environment, or None if no environment is available.

    Returns:
        A dict mapping host path (or Docker volume name) to container mount
        path. Empty dict when ``env`` is None or no recognized attribute is
        present.

    Notes:
        Duck-typed: looks for ``_volume_name`` (DockerEnvironment) or
        ``root_path`` (LocalEnvironment) on the input object. Concrete
        environment classes live in ``services.environment`` and are not
        imported here to keep ``engine/`` free of services-layer dependencies.
    """
    if env is None:
        return {}

    if hasattr(env, "_volume_name"):
        return {env._volume_name: _SANDBOX_MOUNT_POINT}

    if hasattr(env, "root_path"):
        return {str(env.root_path): _SANDBOX_MOUNT_POINT}

    return {}
