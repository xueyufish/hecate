"""Tests for the load_skill built-in tool (L2 progressive disclosure)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from hecate.tools.skill.loader import SkillNotAdvertisedError
from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS, BuiltInToolExecutor
from hecate.tools.tool.search import SearchProvider


class MockSearchProvider(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        return []


class StubSkillLoader:
    """Records load requests; returns canned content."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, uuid.UUID, uuid.UUID]] = []

    async def load_skill_content(
        self,
        skill_name: str,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
    ) -> str:
        self.calls.append((skill_name, agent_id, workspace_id))
        return f'<skill name="{skill_name}">\nfull content\n</skill>'


AGENT_ID = uuid.uuid4()
WORKSPACE_ID = uuid.uuid4()
CONTEXT = {"agent_id": str(AGENT_ID), "workspace_id": str(WORKSPACE_ID)}


def _executor(skill_loader: object = None) -> BuiltInToolExecutor:
    return BuiltInToolExecutor(
        search_provider=MockSearchProvider(),
        workspace_root=str(Path("./tmp-workspace-load-skill")),
        skill_loader=skill_loader,
    )


class TestLoadSkillDefinition:
    def test_definition_registered(self) -> None:
        assert "load_skill" in BUILTIN_TOOL_DEFINITIONS
        definition = BUILTIN_TOOL_DEFINITIONS["load_skill"]
        assert definition["risk_level"] == "LOW"
        assert "skill_name" in definition["parameters"]["properties"]


class TestLoadSkillExecution:
    async def test_loads_advertised_skill(self) -> None:
        loader = StubSkillLoader()
        result = await _executor(loader).execute("load_skill", {"skill_name": "code-review"}, CONTEXT)

        assert "full content" in result
        assert loader.calls == [("code-review", AGENT_ID, WORKSPACE_ID)]

    async def test_fails_closed_without_loader(self) -> None:
        with pytest.raises(ValueError, match="not available"):
            await _executor(None).execute("load_skill", {"skill_name": "x"}, CONTEXT)

    async def test_fails_closed_without_agent_context(self) -> None:
        loader = StubSkillLoader()
        with pytest.raises(ValueError, match="agent context"):
            await _executor(loader).execute("load_skill", {"skill_name": "x"}, {})

    async def test_rejects_empty_skill_name(self) -> None:
        loader = StubSkillLoader()
        with pytest.raises(ValueError, match="skill_name"):
            await _executor(loader).execute("load_skill", {"skill_name": " "}, CONTEXT)

    async def test_unadvertised_skill_surfaces_informative_error(self) -> None:
        class RejectingLoader(StubSkillLoader):
            async def load_skill_content(self, skill_name, agent_id, workspace_id, session_id=None):
                raise SkillNotAdvertisedError(f"Skill '{skill_name}' is not advertised for agent {agent_id}")

        with pytest.raises(ValueError, match="not advertised"):
            await _executor(RejectingLoader()).execute("load_skill", {"skill_name": "ghost"}, CONTEXT)
