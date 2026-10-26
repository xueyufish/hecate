"""Unit tests for PromptService."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
from hecate.studio.prompts.service import PromptService

WS = uuid.UUID(int=0)


@pytest.mark.asyncio
async def test_create_prompt(db_session: AsyncSession) -> None:
    """Test creating a prompt."""
    service = PromptService(db_session)

    data = PromptCreateSchema(
        name="test-prompt",
        template="Hello {{ name }}!",
        variables=["name"],
        labels=["production"],
    )

    result = await service.create_prompt(data, WS, user_role="admin")

    assert result.name == "test-prompt"
    assert result.current_version == 1
    assert result.version is not None
    assert result.version.template == "Hello {{ name }}!"
    assert "name" in result.version.variables


@pytest.mark.asyncio
async def test_create_prompt_auto_extract_variables(db_session: AsyncSession) -> None:
    """Test that variables are auto-extracted from template."""
    service = PromptService(db_session)

    data = PromptCreateSchema(
        name="test-prompt",
        template="Hello {{ name }}, welcome to {{ place }}!",
    )

    result = await service.create_prompt(data, WS)

    assert result.version is not None
    assert "name" in result.version.variables
    assert "place" in result.version.variables


@pytest.mark.asyncio
async def test_get_prompt(db_session: AsyncSession) -> None:
    """Test getting a prompt."""
    service = PromptService(db_session)

    data = PromptCreateSchema(name="test", template="Hello!")
    created = await service.create_prompt(data, WS)

    result = await service.get_prompt(created.id, WS)

    assert result.id == created.id
    assert result.name == "test"


@pytest.mark.asyncio
async def test_get_prompt_not_found(db_session: AsyncSession) -> None:
    """Test getting a non-existent prompt raises LookupError."""
    service = PromptService(db_session)

    with pytest.raises(LookupError, match="not found"):
        await service.get_prompt(uuid.uuid4(), WS)


@pytest.mark.asyncio
async def test_update_prompt_template(db_session: AsyncSession) -> None:
    """Test updating prompt template creates new version."""
    service = PromptService(db_session)

    data = PromptCreateSchema(name="test", template="Hello!")
    created = await service.create_prompt(data, WS)

    update = PromptUpdateSchema(template="Hello {{ name }}!")
    result = await service.update_prompt(created.id, update, WS)

    assert result.current_version == 2
    assert result.version is not None
    assert result.version.template == "Hello {{ name }}!"


@pytest.mark.asyncio
async def test_delete_prompt(db_session: AsyncSession) -> None:
    """Test soft deleting a prompt."""
    service = PromptService(db_session)

    data = PromptCreateSchema(name="test", template="Hello!")
    created = await service.create_prompt(data, WS)

    await service.delete_prompt(created.id, WS)

    with pytest.raises(LookupError, match="not found"):
        await service.get_prompt(created.id, WS)


@pytest.mark.asyncio
async def test_list_prompts(db_session: AsyncSession) -> None:
    """Test listing prompts."""
    service = PromptService(db_session)

    for i in range(3):
        data = PromptCreateSchema(name=f"prompt-{i}", template=f"Template {i}")
        await service.create_prompt(data, WS)

    result = await service.list_prompts(WS)

    assert result["total"] == 3
    assert len(result["items"]) == 3


@pytest.mark.asyncio
async def test_list_versions(db_session: AsyncSession) -> None:
    """Test listing prompt versions."""
    service = PromptService(db_session)

    data = PromptCreateSchema(name="test", template="V1")
    created = await service.create_prompt(data, WS)

    await service.update_prompt(created.id, PromptUpdateSchema(template="V2"), WS)
    await service.update_prompt(created.id, PromptUpdateSchema(template="V3"), WS)

    versions = await service.list_versions(created.id, WS)

    assert len(versions) == 3
    assert versions[0].template == "V1"
    assert versions[1].template == "V2"
    assert versions[2].template == "V3"


@pytest.mark.asyncio
async def test_rollback_to_version(db_session: AsyncSession) -> None:
    """Test rolling back to a previous version."""
    service = PromptService(db_session)

    data = PromptCreateSchema(name="test", template="V1")
    created = await service.create_prompt(data, WS)

    await service.update_prompt(created.id, PromptUpdateSchema(template="V2"), WS)

    result = await service.rollback_to_version(created.id, 1, WS)

    assert result.current_version == 3
    assert result.version is not None
    assert result.version.template == "V1"


@pytest.mark.asyncio
async def test_get_by_label(db_session: AsyncSession) -> None:
    """Test getting prompt by deployment label."""
    service = PromptService(db_session)

    data = PromptCreateSchema(
        name="test",
        template="Hello!",
        labels=["production"],
    )
    await service.create_prompt(data, WS, user_role="admin")

    result = await service.get_by_label("production", WS)

    assert result is not None
    assert result.name == "test"


@pytest.mark.asyncio
async def test_get_by_label_not_found(db_session: AsyncSession) -> None:
    """Test getting prompt by non-existent label returns None."""
    service = PromptService(db_session)

    result = await service.get_by_label("nonexistent", WS)

    assert result is None
