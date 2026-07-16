"""Agent Environment abstraction — unified agent execution environment.

Provides AgentEnvironment ABC and LocalEnvironment implementation for
managing agent's persistent execution context (files, memory, skills).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from hecate.core.config import settings


@dataclass
class FileInfo:
    """Information about a file in the environment.

    Attributes:
        name: File name.
        path: Relative path from environment root.
        size: File size in bytes.
        modified_at: Last modification timestamp (Unix epoch).
        is_dir: Whether this is a directory.
    """

    name: str
    path: str
    size: int
    modified_at: float
    is_dir: bool


class AgentEnvironment(ABC):
    """Abstract agent execution environment.

    Represents the agent's persistent execution context with file management,
    lifecycle, and session association. Conceptually distinct from WorkspaceModel
    (multi-tenant isolation boundary) — AgentEnvironment is the agent's working
    context, not the tenant boundary.
    """

    @property
    @abstractmethod
    def environment_id(self) -> str:
        """Unique identifier for this environment (typically agent_id)."""

    @property
    @abstractmethod
    def root_path(self) -> Path:
        """Root directory path of the environment."""

    @abstractmethod
    async def read_file(self, path: str) -> bytes:
        """Read a file from the environment.

        Args:
            path: Relative path from environment root.

        Returns:
            File content as bytes.

        Raises:
            FileNotFoundError: If the file does not exist.
        """

    @abstractmethod
    async def write_file(self, path: str, content: bytes) -> None:
        """Write a file to the environment.

        Args:
            path: Relative path from environment root.
            content: File content as bytes.
        """

    @abstractmethod
    async def list_files(self, path: str = "") -> list[FileInfo]:
        """List files in a directory.

        Args:
            path: Relative directory path from environment root. Empty = root.

        Returns:
            List of FileInfo for files and subdirectories.
        """

    @abstractmethod
    async def delete_file(self, path: str) -> None:
        """Delete a file from the environment.

        Args:
            path: Relative path from environment root.

        Raises:
            FileNotFoundError: If the file does not exist.
        """

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check if a file or directory exists.

        Args:
            path: Relative path from environment root.

        Returns:
            True if the path exists.
        """

    @abstractmethod
    async def ensure_dirs(self) -> None:
        """Ensure all required subdirectories exist.

        Creates: sessions/, files/, memory/, skills/
        """


class LocalEnvironment(AgentEnvironment):
    """Local filesystem implementation of AgentEnvironment.

    Stores agent data at {WORKSPACE_ROOT}/{agent_id}/ with subdirectories:
    - sessions/ — conversation logs
    - files/ — agent-generated files
    - memory/ — long-term memory (4.25)
    - skills/ — skill configurations

    Args:
        agent_id: The agent identifier.
        root: Root directory path (default: settings.WORKSPACE_ROOT).
    """

    SUBDIRS = ("sessions", "files", "memory", "skills")

    def __init__(self, agent_id: str, root: str | None = None) -> None:
        self._agent_id = agent_id
        self._root = Path(root or settings.WORKSPACE_ROOT) / agent_id

    @property
    def environment_id(self) -> str:
        return self._agent_id

    @property
    def root_path(self) -> Path:
        return self._root

    async def read_file(self, path: str) -> bytes:
        full_path = self._resolve(path)
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return full_path.read_bytes()

    async def write_file(self, path: str, content: bytes) -> None:
        full_path = self._resolve(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(content)

    async def list_files(self, path: str = "") -> list[FileInfo]:
        dir_path = self._resolve(path)
        if not dir_path.is_dir():
            raise FileNotFoundError(f"Directory not found: {path}")
        result = []
        resolved_root = self._root.resolve()
        for entry in sorted(dir_path.iterdir()):
            stat = entry.stat()
            result.append(
                FileInfo(
                    name=entry.name,
                    path=str(entry.resolve().relative_to(resolved_root)),
                    size=stat.st_size,
                    modified_at=stat.st_mtime,
                    is_dir=entry.is_dir(),
                )
            )
        return result

    async def delete_file(self, path: str) -> None:
        full_path = self._resolve(path)
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        full_path.unlink()

    async def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    async def ensure_dirs(self) -> None:
        for subdir in self.SUBDIRS:
            (self._root / subdir).mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        """Resolve a relative path to an absolute path within the environment.

        Prevents path traversal attacks by ensuring the resolved path
        stays within the environment root.
        """
        resolved = (self._root / path).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise ValueError(f"Path traversal detected: {path}")
        return resolved
