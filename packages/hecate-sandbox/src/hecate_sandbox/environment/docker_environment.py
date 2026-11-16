"""Docker-backed AgentEnvironment implementation.

Provides DockerEnvironment — an AgentEnvironment backed by a Docker
container with a named volume for persistent filesystem isolation.

Each agent gets its own long-running container with volume
``agent-{agent_id}`` mounted at ``/env``. File operations use Docker's
``exec`` / ``get_archive`` / ``put_archive`` APIs via the ``aiodocker``
async client.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import posixpath
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hecate.core.config import settings
from hecate_sandbox.environment.credential_scope import CredentialScope
from hecate_sandbox.environment.environment import (
    AgentEnvironment,
    ExecResult,
    FileInfo,
)
from hecate_sandbox.environment.network_policy import (
    NetworkEgressPolicy,
    NetworkPolicyMode,
)

if TYPE_CHECKING:
    import aiodocker

logger = logging.getLogger(__name__)

_CONTAINER_WORKDIR = "/env"


class DockerEnvironment(AgentEnvironment):
    """AgentEnvironment backed by a Docker container.

    Each agent gets a dedicated container with a named volume
    (``agent-{agent_id}``) mounted at ``/env`` containing
    ``sessions/``, ``files/``, ``memory/``, ``skills/``.

    Args:
        agent_id: The agent identifier.
        image: Docker image to use (default: ``settings.DOCKER_AGENT_IMAGE``).
        runtime: OCI runtime (``"runc"`` or ``"runsc"``).
        network_mode: Docker network mode (default: ``settings.DOCKER_NETWORK_MODE``).
        docker_url: Docker daemon URL. ``None`` = default socket.
    """

    SUBDIRS = ("sessions", "files", "memory", "skills")

    def __init__(
        self,
        agent_id: str,
        *,
        image: str | None = None,
        runtime: str | None = None,
        network_mode: str | None = None,
        docker_url: str | None = None,
        network_policy: NetworkEgressPolicy | None = None,
        credential_scope: CredentialScope | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._image = image or settings.DOCKER_AGENT_IMAGE
        self._runtime = runtime or settings.DOCKER_RUNTIME
        self._network_mode = network_mode or settings.DOCKER_NETWORK_MODE
        if self._network_mode == "default":
            self._network_mode = "bridge"
        self._docker_url = docker_url
        self._container_name = f"hecate-agent-{agent_id}"
        self._volume_name = f"agent-{agent_id}"
        self._client: aiodocker.Docker | None = None
        self._container: Any = None
        self._started = False
        self._retired = False
        self._lifecycle_lock = asyncio.Lock()
        self._network_policy = network_policy or NetworkEgressPolicy(
            mode=NetworkPolicyMode(settings.AGENT_ENV_NETWORK_POLICY),
        )
        self._credential_scope = credential_scope or CredentialScope(
            enabled=settings.AGENT_ENV_CREDENTIAL_SCOPING,
        )

    @property
    def environment_id(self) -> str:
        return self._agent_id

    @property
    def root_path(self) -> Path:
        return Path(_CONTAINER_WORKDIR)

    async def _ensure_started(self) -> None:
        """Connect and start a container verified against the configured policy."""
        async with self._lifecycle_lock:
            if self._retired:
                raise RuntimeError("Docker environment was retired after a security policy change")
            if self._started:
                return
            if self._network_policy.mode == NetworkPolicyMode.DENY_ALL and self._network_policy.allowed_domains:
                raise RuntimeError("Docker deny_all requires an empty egress allow-list")
            if self._client is None:
                import aiodocker

                self._client = aiodocker.Docker(url=self._docker_url)
            try:
                await self._ensure_container()
            except BaseException:
                with contextlib.suppress(Exception):
                    await self._client.close()
                self._client = None
                self._container = None
                raise
            self._started = True

    async def _ensure_container(self) -> None:
        """Reuse a verified container or replace a known stale agent container."""
        from aiodocker.exceptions import DockerError

        if self._client is None:
            raise RuntimeError("Docker client not initialized")
        try:
            container = await self._client.containers.get(self._container_name)
        except DockerError as exc:
            if exc.status != 404:
                raise
            await self._create_container()
            return

        info = await container.show()
        mismatch = await self._container_mismatch(info)
        if mismatch is not None:
            logger.warning("Replacing container %s: %s", self._container_name, mismatch)
            try:
                await container.delete(force=True)
            except DockerError as exc:
                if exc.status != 404:
                    raise
            await self._create_container()
            return

        if info["State"]["Status"] != "running":
            await container.start()
        self._container = container

    async def _container_mismatch(self, info: Any) -> str | None:
        """Refuse incomplete records and identify a verified agent container's policy mismatch."""
        label = self._container_name
        if not isinstance(info, dict):
            raise RuntimeError(f"cannot verify container {label}: invalid inspect record")
        config = info.get("Config")
        state = info.get("State")
        mounts = info.get("Mounts")
        if (
            not isinstance(config, dict)
            or not isinstance(config.get("Image"), str)
            or not isinstance(state, dict)
            or not isinstance(state.get("Status"), str)
            or not isinstance(mounts, list)
            or any(not isinstance(mount, dict) for mount in mounts)
        ):
            raise RuntimeError(f"cannot verify container {label}: incomplete inspect record")
        env_mounts = [mount for mount in mounts if mount.get("Destination") == _CONTAINER_WORKDIR]
        if len(env_mounts) != 1 or (
            env_mounts[0].get("Type") != "volume"
            or env_mounts[0].get("Name") != self._volume_name
            or env_mounts[0].get("RW") is not True
        ):
            raise RuntimeError(f"cannot verify container {label}: expected writable agent volume at /env")

        host = info.get("HostConfig")
        if not isinstance(host, dict) or not isinstance(host.get("NetworkMode"), str):
            raise RuntimeError(f"cannot verify container {label}: missing host network configuration")
        deny_all = self._network_policy.mode == NetworkPolicyMode.DENY_ALL
        if (deny_all or self._credential_scope.enabled) and (
            len(mounts) != 1
            or any(host.get(key) for key in ("Privileged", "PidMode", "Devices", "CapAdd"))
            or host.get("IpcMode") == "host"
        ):
            return "container has extra mounts, privileges or shared host namespaces"
        if deny_all:
            network_settings = info.get("NetworkSettings")
            networks = network_settings.get("Networks") if isinstance(network_settings, dict) else None
            if not isinstance(networks, dict) or not networks:
                raise RuntimeError(f"cannot verify container {label}: missing network attachments")
            if host["NetworkMode"] != "none" or set(networks) != {"none"}:
                return "deny_all requires Docker's none network"
        elif host["NetworkMode"] != self._network_mode:
            return "container network mode differs from the configured mode"
        if config["Image"] != self._image:
            return "container image differs from the configured image"
        if self._runtime and host.get("Runtime") != self._runtime:
            return "container runtime differs from the configured runtime"
        if self._credential_scope.enabled:
            if self._client is None or not isinstance(info.get("Image"), str):
                raise RuntimeError(f"cannot verify container {label}: missing image identity")
            image = await self._client.images.inspect(info["Image"])
            image_config = image.get("Config")
            if not isinstance(image_config, dict) or "Env" not in config or "Env" not in image_config:
                raise RuntimeError(f"cannot verify container {label}: missing environment configuration")
            if (config["Env"] or []) != (image_config["Env"] or []):
                return "container environment differs from the image defaults"
        return None

    async def _create_container(self) -> None:
        """Create a verified container with the agent's persistent volume."""
        if self._client is None:
            raise RuntimeError("Docker client not initialized")

        await self._client.volumes.create({"Name": self._volume_name})
        network = "none" if self._network_policy.mode == NetworkPolicyMode.DENY_ALL else self._network_mode
        create_config: dict[str, Any] = {
            "Image": self._image,
            "Cmd": ["sleep", "infinity"],
            "WorkingDir": _CONTAINER_WORKDIR,
            "HostConfig": {
                "Mounts": [
                    {
                        "Type": "volume",
                        "Source": self._volume_name,
                        "Target": _CONTAINER_WORKDIR,
                    },
                ],
                "NetworkMode": network,
            },
        }
        if self._runtime:
            create_config["HostConfig"]["Runtime"] = self._runtime

        container = await self._client.containers.create(create_config, name=self._container_name)
        try:
            mismatch = await self._container_mismatch(await container.show())
            if mismatch is not None:
                raise RuntimeError(f"new container {self._container_name} failed policy verification: {mismatch}")
            await container.start()
        except BaseException:
            with contextlib.suppress(Exception):
                await container.delete(force=True)
            raise
        self._container = container

    async def read_file(self, path: str) -> bytes:
        await self._ensure_started()
        if self._container is None:
            raise RuntimeError("Container not initialized")

        full_path = posixpath.join(_CONTAINER_WORKDIR, path)
        try:
            tar_stream = await self._container.get_archive(full_path)
        except Exception as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                raise FileNotFoundError(f"File not found: {path}") from exc
            raise

        # ``get_archive`` returns an already-opened ``tarfile.TarFile`` in
        # aiodocker >= 0.23; older versions returned raw bytes wrapped in
        # BytesIO. Accept both shapes for compatibility.
        if isinstance(tar_stream, tarfile.TarFile):
            tf_cm: tarfile.TarFile = tar_stream
            with tf_cm as tf:
                for member in tf.getmembers():
                    if member.isfile():
                        f = tf.extractfile(member)
                        if f is not None:
                            return f.read()
            raise FileNotFoundError(f"File not found: {path}")

        with tarfile.open(fileobj=io.BytesIO(tar_stream), mode="r") as tf:
            for member in tf.getmembers():
                if member.isfile():
                    f = tf.extractfile(member)
                    if f is not None:
                        return f.read()

        raise FileNotFoundError(f"File not found: {path}")

    async def write_file(self, path: str, content: bytes) -> None:
        await self._ensure_started()
        if self._container is None:
            raise RuntimeError("Container not initialized")

        parent = posixpath.dirname(path)
        if parent:
            await self.exec_shell(["mkdir", "-p", posixpath.join(_CONTAINER_WORKDIR, parent)])

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=posixpath.basename(path))
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))

        target_dir = posixpath.join(_CONTAINER_WORKDIR, parent) if parent else _CONTAINER_WORKDIR
        await self._container.put_archive(target_dir, buf.getvalue())

    async def list_files(self, path: str = "") -> list[FileInfo]:
        await self._ensure_started()

        target = posixpath.join(_CONTAINER_WORKDIR, path) if path else _CONTAINER_WORKDIR
        result = await self.exec_shell(["ls", "-la", "--time-style=+%s", target])

        if result.exit_code != 0:
            raise FileNotFoundError(f"Directory not found: {path}")

        files: list[FileInfo] = []
        for line in result.stdout.decode(errors="replace").splitlines()[1:]:
            # ``ls -la --time-style=+%s`` emits perms, links, owner, group,
            # size, single unix-timestamp mtime, and name — exactly 7
            # whitespace-separated fields. Use rsplit so the file name
            # (which may itself contain spaces) is always the last field.
            if not line.strip():
                continue
            parts = line.split(None, 6)
            if len(parts) < 7:
                continue
            perms, _, _, _, size_str, mtime_str, name = parts
            if name in (".", ".."):
                continue
            is_dir = perms.startswith("d")
            try:
                size = int(size_str)
            except ValueError:
                size = 0
            try:
                modified_at = float(mtime_str)
            except ValueError:
                modified_at = 0.0
            rel_path = posixpath.join(path, name) if path else name
            files.append(
                FileInfo(
                    name=name,
                    path=rel_path,
                    size=size,
                    modified_at=modified_at,
                    is_dir=is_dir,
                )
            )
        return files

    async def delete_file(self, path: str) -> None:
        await self._ensure_started()
        full_path = posixpath.join(_CONTAINER_WORKDIR, path)
        result = await self.exec_shell(["rm", "-f", full_path])
        if result.exit_code != 0:
            raise FileNotFoundError(f"File not found: {path}")

    async def exists(self, path: str) -> bool:
        await self._ensure_started()
        full_path = posixpath.join(_CONTAINER_WORKDIR, path)
        result = await self.exec_shell(["test", "-e", full_path])
        return result.exit_code == 0

    async def ensure_dirs(self) -> None:
        await self._ensure_started()
        dirs = [posixpath.join(_CONTAINER_WORKDIR, s) for s in self.SUBDIRS]
        result = await self.exec_shell(["mkdir", "-p", *dirs])
        if result.exit_code != 0:
            raise RuntimeError(f"could not initialize agent environment directories (exit {result.exit_code})")

    async def exec_shell(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        await self._ensure_started()
        if self._container is None:
            raise RuntimeError("Container not initialized")

        workdir = posixpath.join(_CONTAINER_WORKDIR, cwd) if cwd else _CONTAINER_WORKDIR

        exec_kwargs: dict[str, Any] = {
            "cmd": command,
            "workdir": workdir,
            "stdout": True,
            "stderr": True,
        }

        # Host environment variables can carry secrets under arbitrary
        # names. Docker exec uses the verified container environment.
        try:
            exec_obj = await self._container.exec(**exec_kwargs)
        except Exception as exc:
            return ExecResult(exit_code=-1, stdout=b"", stderr=str(exc).encode())

        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []

        async def _run() -> tuple[int, bytes, bytes]:
            async with exec_obj.start() as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    if msg.stream == 1:
                        stdout_parts.append(msg.data)
                    else:
                        stderr_parts.append(msg.data)
            inspect = await exec_obj.inspect()
            code = inspect.get("ExitCode", -1)
            return (
                int(code) if code is not None else -1,
                b"".join(stdout_parts),
                b"".join(stderr_parts),
            )

        try:
            code, out, err = await asyncio.wait_for(_run(), timeout=timeout)
        except TimeoutError:
            return ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=f"command timed out after {timeout}s".encode(),
            )

        return ExecResult(exit_code=code, stdout=out, stderr=err)

    async def stop(self) -> None:
        """Stop the container without destroying it (for warm pool)."""
        if self._container is not None:
            try:
                await self._container.stop()
            except Exception as exc:
                logger.warning("Failed to stop container %s: %s", self._container_name, exc)
        self._started = False

    async def start(self) -> None:
        """Verify and start the container, including when reused from the warm pool."""
        await self._ensure_started()

    async def retire(self) -> None:
        """Prevent a superseded environment handle from starting another container."""
        async with self._lifecycle_lock:
            self._retired = True
            await self.remove()

    async def remove(self) -> None:
        """Destroy the container (volume persists for future reuse)."""
        if self._container is not None:
            try:
                await self._container.delete(force=True)
            except Exception as exc:
                logger.warning("Failed to delete container %s: %s", self._container_name, exc)
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.close()
        self._container = None
        self._client = None
        self._started = False
