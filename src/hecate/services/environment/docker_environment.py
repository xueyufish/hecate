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
from hecate.services.environment.environment import (
    AgentEnvironment,
    ExecResult,
    FileInfo,
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
    ) -> None:
        self._agent_id = agent_id
        self._image = image or settings.DOCKER_AGENT_IMAGE
        self._runtime = runtime or settings.DOCKER_RUNTIME
        self._network_mode = network_mode or settings.DOCKER_NETWORK_MODE
        self._docker_url = docker_url
        self._container_name = f"hecate-agent-{agent_id}"
        self._volume_name = f"agent-{agent_id}"
        self._client: aiodocker.Docker | None = None
        self._container: Any = None
        self._started = False

    @property
    def environment_id(self) -> str:
        return self._agent_id

    @property
    def root_path(self) -> Path:
        return Path(_CONTAINER_WORKDIR)

    async def _ensure_started(self) -> None:
        """Ensure the Docker client is connected and container is running."""
        if self._started:
            return
        if self._client is None:
            import aiodocker

            self._client = aiodocker.Docker(url=self._docker_url)

        await self._ensure_container()
        self._started = True

    async def _ensure_container(self) -> None:
        """Find an existing container by name or create a new one."""
        if self._client is None:
            raise RuntimeError("Docker client not initialized")

        try:
            self._container = await self._client.containers.get(self._container_name)
            state = self._container._container.get("State", {})
            if state.get("Status") != "running":
                await self._container.start()
            logger.debug("Reusing container %s for agent %s", self._container_name, self._agent_id)
            return
        except Exception as exc:
            logger.debug("Container %s not found, creating: %s", self._container_name, exc)

        await self._create_container()

    async def _create_container(self) -> None:
        """Create and start a new container with named volume."""
        if self._client is None:
            raise RuntimeError("Docker client not initialized")

        try:
            await self._client.volumes.create({"Name": self._volume_name})
        except Exception as exc:
            logger.debug("Volume %s may already exist: %s", self._volume_name, exc)

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
                "NetworkMode": self._network_mode,
            },
        }

        if self._runtime and self._runtime != "runc":
            create_config["HostConfig"]["Runtime"] = self._runtime

        self._container = await self._client.containers.create_or_replace(
            name=self._container_name,
            config=create_config,
        )
        await self._container.start()
        logger.info("Created container %s for agent %s", self._container_name, self._agent_id)

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
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            perms, _, _, _, size_str, mtime_str, _time, _name_full = parts[:7] if len(parts) >= 8 else parts
            name = parts[-1]
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
        await self.exec_shell(["mkdir", "-p", *dirs])

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

        try:
            exec_obj = await self._container.exec(
                cmd=command,
                workdir=workdir,
                stdout=True,
                stderr=True,
            )
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
        """Restart a stopped container (for warm pool reuse)."""
        if self._container is not None:
            try:
                await self._container.start()
                self._started = True
            except Exception as exc:
                logger.warning("Failed to start container %s: %s", self._container_name, exc)

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
