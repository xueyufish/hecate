"""REST API for agent environment file management."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, UploadFile

from hecate.services.environment.manager import EnvironmentManager

router = APIRouter(prefix="/api/agents/{agent_id}/environment", tags=["environment"])

_manager: EnvironmentManager | None = None


def get_environment_manager() -> EnvironmentManager:
    """Get or create the module-level EnvironmentManager."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        _manager = EnvironmentManager()
    return _manager


@router.get("/files")
async def list_files(
    agent_id: str,
    path: str = "",
) -> list[dict]:
    """List files in an agent's environment.

    Args:
        agent_id: The agent identifier.
        path: Subdirectory path (default: root).
    """
    manager = get_environment_manager()
    env = await manager.get_or_create(agent_id)
    files = await env.list_files(path)
    return [
        {
            "name": f.name,
            "path": f.path,
            "size": f.size,
            "modified_at": f.modified_at,
            "is_dir": f.is_dir,
        }
        for f in files
    ]


@router.get("/files/{path:path}")
async def read_file(
    agent_id: str,
    path: str,
) -> dict:
    """Read a file from an agent's environment.

    Args:
        agent_id: The agent identifier.
        path: File path relative to environment root.
    """
    manager = get_environment_manager()
    env = await manager.get_or_create(agent_id)
    try:
        content = await env.read_file(f"files/{path}")
        return {"path": path, "content": content.decode(errors="replace")}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"File not found: {path}") from e


@router.post("/files")
async def write_file(
    agent_id: str,
    file: UploadFile,
) -> dict:
    """Upload a file to an agent's environment.

    Args:
        agent_id: The agent identifier.
        file: The uploaded file.
    """
    manager = get_environment_manager()
    env = await manager.get_or_create(agent_id)
    content = await file.read()
    await env.write_file(f"files/{file.filename}", content)
    return {"path": f"files/{file.filename}", "size": len(content)}


@router.delete("/files/{path:path}")
async def delete_file(
    agent_id: str,
    path: str,
) -> dict:
    """Delete a file from an agent's environment.

    Args:
        agent_id: The agent identifier.
        path: File path relative to environment root.
    """
    manager = get_environment_manager()
    env = await manager.get_or_create(agent_id)
    try:
        await env.delete_file(f"files/{path}")
        return {"deleted": path}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"File not found: {path}") from e


@router.get("/stats")
async def get_stats(
    agent_id: str,
) -> dict:
    """Get environment statistics.

    Args:
        agent_id: The agent identifier.
    """
    manager = get_environment_manager()
    env = await manager.get_or_create(agent_id)
    files = await env.list_files("files")
    total_size = sum(f.size for f in files if not f.is_dir)
    return {
        "agent_id": agent_id,
        "environment_id": env.environment_id,
        "root_path": str(env.root_path),
        "file_count": len([f for f in files if not f.is_dir]),
        "total_size_bytes": total_size,
    }
