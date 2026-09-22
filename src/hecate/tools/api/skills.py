"""Skill management API endpoints.

Provides CRUD operations for skills:
- ``POST /api/skills`` — Create a new skill
- ``GET /api/skills`` — List skills (paginated, workspace-scoped)
- ``GET /api/skills/{id}`` — Get skill by ID
- ``PUT /api/skills/{id}`` — Update skill
- ``DELETE /api/skills/{id}`` — Soft delete skill
- ``POST /api/skills/import`` — Import SKILL.md file
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.canonical_hash import canonical_hash
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.skill import SkillCreateSchema, SkillModel, SkillReadSchema, SkillUpdateSchema
from hecate.tools.skill.dependency_validator import (
    validate_requires,
)
from hecate.tools.skill.provider_registry import PROVIDER_BUNDLED, derive_provider

router = APIRouter()

#: Identity for the bundled workspace used by deps lookups; mirrors the
#: convention in :mod:`hecate.tools.skill.dependency_resolver`.
BUNDLED_WORKSPACE_ID = uuid.UUID(int=0)


def _compute_content_hash(skill: SkillModel) -> str:
    """Hash the content-field set shared with agent-version ref manifests."""
    return canonical_hash(
        {
            "name": skill.name,
            "instructions": skill.instructions,
            "allowed_tools": skill.allowed_tools,
            "scripts": skill.scripts,
            "references": skill.references,
        }
    )


async def _attach_version_status(db: AsyncSession, payload: dict) -> dict:
    """Augment a skill read payload with 5.9d versioning status fields."""
    from hecate.tools.skill.versioning import SkillVersionService

    service = SkillVersionService(db)
    try:
        status = await service.get_status(uuid.UUID(str(payload["id"])))
    except Exception:  # noqa: BLE001 - versioning must never break read paths
        status = {"latest_version": None, "has_uncommitted_changes": True}
    payload["latest_version"] = status["latest_version"]
    payload["has_uncommitted_changes"] = status["has_uncommitted_changes"]
    return payload


def _derive_trust_tier(provider: str | None) -> str:
    """Platform-managed tier: bundled skills are official, everything else community."""
    return "official" if provider == PROVIDER_BUNDLED else "community"


def _collect_requires_names(requires: list[dict] | None) -> list[str]:
    """Flatten a ``requires`` payload into a deduplicated name list.

    Used by the API validation path to drive a single bulk ``WHERE name IN``
    query, avoiding per-node round trips when the validator walks the
    transitive closure.
    """
    if not requires:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for entry in requires:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name not in seen:
                seen.add(name)
                out.append(name)
    return out


async def _build_deps_view(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    skill_name: str,
    mode: str,
    *,
    reverse: bool = False,
    transitive: bool = True,
) -> dict:
    """Build a deps view (direct / graph / closure / reverse) for a skill.

    Modes:
        ``direct`` — list of direct ``requires`` entries (no traversal).
        ``graph``  — tree of {name -> children} flattened.
        ``closure`` — full transitive list of resolved skill names.
        ``reverse`` — list of skills whose ``requires`` includes this one.

    The closure walk delegates to the resolver so the same logic serves
    authoring validation and the diagnostic CLI.
    """
    if mode not in {"direct", "graph", "closure", "reverse"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "INVALID_MODE", "message": f"unknown mode {mode!r}"}},
        )

    if mode == "reverse":
        # Reverse lookup: who requires this skill? Direct + transitive
        # when ``transitive=True``, direct-only when ``reverse-direct``.
        result = await db.execute(
            select(SkillModel).where(
                SkillModel.workspace_id.in_([workspace_id, BUNDLED_WORKSPACE_ID]),
                SkillModel.requires.is_not(None),
                ~SkillModel.deleted,
            )
        )
        reverse_hits: list[str] = []
        for row in result.scalars():
            if any(isinstance(e, dict) and e.get("name") == skill_name for e in (row.requires or [])):
                reverse_hits.append(row.name)
        return {"mode": "reverse", "skill": skill_name, "depends_on_me": sorted(reverse_hits)}

    # Forward modes
    result = await db.execute(
        select(SkillModel).where(
            SkillModel.name == skill_name,
            SkillModel.workspace_id.in_([workspace_id, BUNDLED_WORKSPACE_ID]),
            ~SkillModel.deleted,
        )
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": f"skill {skill_name!r} not found"}},
        )

    direct_requires = list(skill.requires or [])
    if mode == "direct":
        return {"mode": "direct", "skill": skill_name, "requires": direct_requires}

    if mode == "graph":
        # Tree shape: each node carries its direct requires.
        visited: set[str] = set()
        order: list[str] = []

        async def _walk(name: str) -> dict:
            if name in visited:
                return {"name": name, "children": []}
            visited.add(name)
            order.append(name)
            r = await db.execute(
                select(SkillModel).where(
                    SkillModel.name == name,
                    SkillModel.workspace_id.in_([workspace_id, BUNDLED_WORKSPACE_ID]),
                    ~SkillModel.deleted,
                )
            )
            node = r.scalar_one_or_none()
            if node is None:
                return {"name": name, "children": [], "missing": True}
            children = []
            for entry in _coerce_entry_list(node.requires):
                children.append(await _walk(entry["name"]))
            return {"name": name, "children": children}

        tree = await _walk(skill_name)
        return {"mode": "graph", "root": tree, "order": order}

    if mode == "closure":
        from hecate.tools.skill.dependency_resolver import (
            ClosureError,
            resolve_closure,
        )

        try:
            closure = await resolve_closure(
                direct_skill_names=[skill_name],
                workspace_id=workspace_id,
                db=db,
            )
        except ClosureError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": {
                        "code": "CLOSURE_INCOMPLETE",
                        "message": str(exc),
                        "details": {"missing": exc.missing},
                    }
                },
            ) from None
        return {
            "mode": "closure",
            "skill": skill_name,
            "closure": [
                {
                    "skill_id": str(r.skill_id),
                    "name": r.name,
                    "provider": r.provider,
                    "version": r.version,
                    "content_hash": r.content_hash,
                }
                for r in closure
            ],
        }

    # Unreachable: validated above.
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": {"code": "INVALID_MODE", "message": f"unknown mode {mode!r}"}},
    )


def _coerce_entry_list(requires: Any) -> list[dict[str, Any]]:
    """Drop non-dict entries from a ``requires`` value (defensive)."""
    if not isinstance(requires, list):
        return []
    return [e for e in requires if isinstance(e, dict)]


async def _validate_requires_or_422(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    skill_name: str,
    source: str | None,
    requires: list[dict] | None,
    excluded_skill_id: uuid.UUID | None = None,
) -> None:
    """Run the dependency validator and translate failures to HTTP 422.

    Builds an in-memory :class:`SkillLookup` adapter that maps names to
    pre-loaded :class:`SkillModel` rows, then delegates to
    :func:`hecate.tools.skill.dependency_validator.validate_requires`.
    The bulk pre-load avoids one SQL query per closure node.

    ``excluded_skill_id`` lets the update path exclude the skill being
    updated from the lookup so a self-reference report only fires when
    the existing stored row really points at itself.
    """
    if not requires:
        return

    name_list = _collect_requires_names(requires)
    if not name_list:
        return

    rows = await db.execute(
        select(SkillModel).where(
            SkillModel.workspace_id == workspace_id,
            SkillModel.name.in_(name_list),
            ~SkillModel.deleted,
        )
    )
    by_name_provider: dict[tuple[str, str | None], SkillModel] = {}
    for row in rows.scalars():
        by_name_provider[(row.name, row.provider)] = row
    if excluded_skill_id is not None:
        for key, row in list(by_name_provider.items()):
            if row.id == excluded_skill_id:
                by_name_provider.pop(key, None)

    class _Adapter:
        def find_skill(
            self,
            ws: uuid.UUID,
            name: str,
            provider: str | None = None,
        ) -> SkillModel | None:
            if provider is not None:
                return by_name_provider.get((name, provider))
            for (n, _prov), row in by_name_provider.items():
                if n == name:
                    return row
            return None

    errors = validate_requires(
        skill_name=skill_name,
        source=source,
        requires=requires,
        workspace_id=workspace_id,
        lookup=_Adapter(),
    )
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "dependency validation failed",
                    "details": {
                        "dependency_errors": [
                            {
                                "error_code": err.error_code,
                                "dependency_path": err.dependency_path,
                                "message": err.message,
                            }
                            for err in errors
                        ],
                    },
                }
            },
        )


async def _get_skill_with_ownership_check(
    db: AsyncSession,
    skill_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> SkillModel:
    """Load a skill and verify workspace ownership.

    Args:
        db: The async database session.
        skill_id: UUID of the skill to load.
        workspace_id: UUID of the requesting workspace.

    Returns:
        The SkillModel if found and owned by workspace.

    Raises:
        HTTPException: 404 if not found, 403 if not owned by workspace.
    """
    result = await db.execute(
        select(SkillModel).where(
            SkillModel.id == skill_id,
            ~SkillModel.deleted,
        )
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Skill not found", "details": None}},
        )

    if skill.workspace_id != workspace_id and skill.workspace_id != uuid.UUID(int=0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Skill belongs to a different workspace",
                    "details": None,
                }
            },
        )

    return skill


@router.post("/skills", status_code=status.HTTP_201_CREATED)
async def create_skill(
    data: SkillCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new skill.

    Args:
        data: The skill creation data.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The created skill data.

    Raises:
        HTTPException: 409 if skill name already exists in workspace.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    provider = derive_provider(data.source)

    # Validate requires graph before persisting — the duplicate-name 409 below
    # would mask a validation error if the new name collided with an existing
    # skill, but the dependency error is the more actionable signal for a
    # user editing the skills field.
    await _validate_requires_or_422(
        db=db,
        workspace_id=workspace_id,
        skill_name=data.name,
        source=data.source,
        requires=data.requires,
    )

    # Same name + same provider collides; cross-provider names coexist (5.9-enh).
    existing = await db.execute(
        select(SkillModel).where(
            SkillModel.name == data.name,
            SkillModel.workspace_id == workspace_id,
            SkillModel.provider == provider,
            ~SkillModel.deleted,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "DUPLICATE_NAME",
                    "message": f"Skill '{data.name}' already exists in this workspace",
                    "details": None,
                }
            },
        )

    skill = SkillModel(
        workspace_id=workspace_id,
        name=data.name,
        description=data.description,
        source=data.source,
        instructions=data.instructions,
        allowed_tools=data.allowed_tools,
        metadata_=data.metadata,
        scripts=data.scripts,
        references=data.references,
        max_tokens=data.max_tokens,
        auto_load=data.auto_load,
        provider=provider,
        trust_tier=_derive_trust_tier(provider),
        model_invocable=data.model_invocable,
        user_invocable=data.user_invocable,
        requires=data.requires,
    )
    db.add(skill)
    await db.flush()
    skill.content_hash = _compute_content_hash(skill)
    await db.flush()
    await db.refresh(skill)
    return SkillReadSchema.model_validate(skill).model_dump()


@router.get("/skills")
async def list_skills(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    source: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List skills with optional source filter and pagination.

    Returns skills from the current workspace plus system skills
    (workspace_id = zero UUID).

    Args:
        db: The async database session.
        ctx: The authenticated context.
        source: Optional filter by skill source (system, user, project).
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with skill list and total count.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)

    # Include workspace skills + system skills
    base_query = select(SkillModel).where(
        ~SkillModel.deleted,
        or_(
            SkillModel.workspace_id == workspace_id,
            SkillModel.workspace_id == uuid.UUID(int=0),
        ),
    )
    if source is not None:
        base_query = base_query.where(SkillModel.source == source)

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(SkillModel.name).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    skills = result.scalars().all()

    return {
        "items": [await _attach_version_status(db, SkillReadSchema.model_validate(s).model_dump()) for s in skills],
        "total": total,
    }


@router.get("/skills/by-name/{skill_name}/deps")
async def get_skill_deps(
    skill_name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    mode: Annotated[str, Query(pattern="^(direct|graph|closure|reverse)$")] = "direct",
    transitive: Annotated[bool, Query(description="Reverse-mode flag; ignored elsewhere")] = True,
) -> dict:
    """Return a deps view for a skill by name.

    Modes:
        ``direct``  — list of direct ``requires`` entries (default).
        ``graph``   — tree of {name -> children} flattened.
        ``closure`` — full transitive list of resolved skill names.
        ``reverse`` — list of skills whose ``requires`` includes this one.
            When ``transitive=False`` is supplied, only direct reverse hits
            are returned.

    Powers the ``hecate skill deps`` CLI subcommand.
    """
    workspace_id = ctx.workspace_id or BUNDLED_WORKSPACE_ID
    return await _build_deps_view(
        db=db,
        workspace_id=workspace_id,
        skill_name=skill_name,
        mode=mode,
        reverse=mode == "reverse",
        transitive=transitive,
    )


@router.get("/skills/{skill_id}")
async def get_skill(
    skill_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a skill by ID.

    Args:
        skill_id: The UUID of the skill to retrieve.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The skill data.

    Raises:
        HTTPException: 404 if skill not found or deleted.
    """
    result = await db.execute(
        select(SkillModel).where(
            SkillModel.id == skill_id,
            ~SkillModel.deleted,
        )
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Skill not found", "details": None}},
        )
    return await _attach_version_status(db, SkillReadSchema.model_validate(skill).model_dump())


@router.put("/skills/{skill_id}")
async def update_skill(
    skill_id: uuid.UUID,
    data: SkillUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an existing skill.

    Args:
        skill_id: The UUID of the skill to update.
        data: The update data (all fields optional).
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The updated skill data.

    Raises:
        HTTPException: 404 if skill not found, 403 if not owned by workspace.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    skill = await _get_skill_with_ownership_check(db, skill_id, workspace_id)

    # Validate requires graph only when the payload changes the field —
    # omitting ``requires`` is a no-op that leaves the stored column alone.
    if "requires" in data.model_fields_set:
        await _validate_requires_or_422(
            db=db,
            workspace_id=workspace_id,
            skill_name=skill.name,
            source=skill.source,
            requires=data.requires,
            excluded_skill_id=skill.id,
        )

    if skill.source == "system":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Cannot modify system skills",
                    "details": None,
                }
            },
        )

    if skill.source == "plugin":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "PLUGIN_MANAGED",
                    "message": "Plugin-derived skills are managed by the owning plugin's lifecycle",
                    "details": None,
                }
            },
        )

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "metadata":
            skill.metadata_ = value
        else:
            setattr(skill, field, value)

    # Final-state consistency: a payload-only flag change can conflict with
    # the stored auto_load value (the schema validator only sees the payload).
    if skill.auto_load and skill.model_invocable is False:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "auto_load requires model_invocable=true",
                    "details": None,
                }
            },
        )

    content_fields = {"name", "instructions", "allowed_tools", "scripts", "references"}
    if content_fields & update_data.keys():
        skill.content_hash = _compute_content_hash(skill)

    await db.flush()
    await db.refresh(skill)
    return await _attach_version_status(db, SkillReadSchema.model_validate(skill).model_dump())


@router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft delete a skill.

    Args:
        skill_id: The UUID of the skill to delete.
        db: The async database session.
        ctx: The authenticated context.

    Raises:
        HTTPException: 404 if skill not found, 403 if not owned by workspace.
    """
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    skill = await _get_skill_with_ownership_check(db, skill_id, workspace_id)

    if skill.source == "system":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Cannot delete system skills",
                    "details": None,
                }
            },
        )

    if skill.source == "plugin":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "PLUGIN_MANAGED",
                    "message": "Plugin-derived skills are managed by the owning plugin's lifecycle",
                    "details": None,
                }
            },
        )

    skill.deleted = True
    skill.deleted_at = datetime.now(UTC)
    await db.flush()


@router.post("/skills/import", status_code=status.HTTP_201_CREATED)
async def import_skill(
    file: Annotated[UploadFile, File(...)],
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Import a skill from a SKILL.md file.

    Accepts a multipart file upload containing a SKILL.md file with
    YAML frontmatter and Markdown body.

    Args:
        file: The uploaded SKILL.md file.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The created skill data.

    Raises:
        HTTPException: 422 for invalid format, 413 for file too large.
    """
    from hecate.tools.skill.parser import parse_skill_md

    # File size check (100KB limit)
    max_file_size = 100 * 1024

    content_bytes = await file.read()
    if len(content_bytes) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": {
                    "code": "FILE_TOO_LARGE",
                    "message": f"File exceeds {max_file_size // 1024}KB limit",
                    "details": None,
                }
            },
        )

    # Decode content
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_ENCODING",
                    "message": "File must be UTF-8 encoded",
                    "details": None,
                }
            },
        ) from None

    # Parse SKILL.md
    try:
        parsed = parse_skill_md(content)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_SKILL_MD",
                    "message": str(e),
                    "details": None,
                }
            },
        ) from None

    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    # Imports create user-origin skills; collide only with the same provider.
    provider = derive_provider(parsed.get("source", "user"))
    import_requires = parsed.get("requires") or []
    await _validate_requires_or_422(
        db=db,
        workspace_id=workspace_id,
        skill_name=parsed["name"],
        source=parsed.get("source", "user"),
        requires=import_requires,
    )
    existing = await db.execute(
        select(SkillModel).where(
            SkillModel.name == parsed["name"],
            SkillModel.workspace_id == workspace_id,
            SkillModel.provider == provider,
            ~SkillModel.deleted,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "DUPLICATE_NAME",
                    "message": f"Skill '{parsed['name']}' already exists in this workspace",
                    "details": None,
                }
            },
        )

    skill = SkillModel(
        workspace_id=workspace_id,
        name=parsed["name"],
        description=parsed["description"],
        source=parsed.get("source", "user"),
        instructions=parsed.get("instructions", ""),
        metadata_=parsed.get("metadata", {}),
        provider=provider,
        trust_tier=_derive_trust_tier(provider),
        requires=import_requires,
    )
    db.add(skill)
    await db.flush()
    skill.content_hash = _compute_content_hash(skill)
    await db.flush()
    await db.refresh(skill)
    return SkillReadSchema.model_validate(skill).model_dump()
