"""Unified Skill Registry service.

Provides a single abstraction for resolving, invoking, and formatting
heterogeneous skill types (Tools, Skills, Knowledge Bases, Workflows,
Agents) without data migration — reads from existing tables.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.skill import SkillModel
from hecate.models.tool import ToolModel
from hecate.models.workflow import WorkflowModel
from hecate.skill_registry.types import (
    ResolvedSkill,
    SkillNotFoundError,
    SkillRef,
    SkillRefType,
)

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Unified skill resolution and invocation service.

    Resolves SkillRef objects to ResolvedSkill by querying existing
    database tables. Zero data migration required.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def resolve(self, refs: list[SkillRef]) -> list[ResolvedSkill]:
        """Resolve a list of SkillRef objects to ResolvedSkill objects.

        Args:
            refs: List of skill references to resolve.

        Returns:
            List of resolved skills in the same order as input refs.

        Raises:
            SkillNotFoundError: If any ref cannot be resolved.
        """
        results: list[ResolvedSkill] = []
        for ref in refs:
            skill = await self._resolve_one(ref)
            results.append(skill)
        return results

    async def _resolve_one(self, ref: SkillRef) -> ResolvedSkill:
        """Resolve a single SkillRef to a ResolvedSkill."""
        match ref.ref_type:
            case SkillRefType.TOOL:
                return await self._resolve_tool(ref.ref_id)
            case SkillRefType.SKILL:
                return await self._resolve_skill(ref.ref_id)
            case SkillRefType.KNOWLEDGE:
                return await self._resolve_knowledge(ref.ref_id)
            case SkillRefType.WORKFLOW:
                return await self._resolve_workflow(ref.ref_id)
            case SkillRefType.AGENT:
                return await self._resolve_agent(ref.ref_id)
            case SkillRefType.REMOTE_AGENT:
                # Remote agent resolution requires A2A client — deferred to Group 4
                raise SkillNotFoundError(ref.ref_type, ref.ref_id)
            case _:
                raise SkillNotFoundError(ref.ref_type, ref.ref_id)

    async def _resolve_tool(self, ref_id: str | UUID) -> ResolvedSkill:
        """Resolve a tool reference by name."""
        name = str(ref_id)
        result = await self._db.execute(select(ToolModel).where(ToolModel.name == name, ToolModel.deleted.is_(False)))
        tool = result.scalar_one_or_none()
        if tool is None:
            raise SkillNotFoundError(SkillRefType.TOOL, ref_id)
        return ResolvedSkill(
            name=tool.name,
            description=tool.description,
            source=SkillRefType.TOOL,
            parameters=tool.parameters,
            metadata={"source": tool.source, "risk_level": tool.risk_level},
        )

    async def _resolve_skill(self, ref_id: str | UUID) -> ResolvedSkill:
        """Resolve a skill reference by name."""
        name = str(ref_id)
        stmt = select(SkillModel).where(SkillModel.name == name, SkillModel.deleted.is_(False))
        result = await self._db.execute(stmt)
        skill = result.scalar_one_or_none()
        if skill is None:
            raise SkillNotFoundError(SkillRefType.SKILL, ref_id)
        return ResolvedSkill(
            name=skill.name,
            description=skill.description,
            source=SkillRefType.SKILL,
            parameters=None,
            metadata={"instructions": skill.instructions, "max_tokens": skill.max_tokens},
        )

    async def _resolve_knowledge(self, ref_id: str | UUID) -> ResolvedSkill:
        """Resolve a knowledge base reference by UUID."""
        kb_id = ref_id if isinstance(ref_id, UUID) else UUID(str(ref_id))
        result = await self._db.execute(
            select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id, KnowledgeBaseModel.deleted.is_(False))
        )
        kb = result.scalar_one_or_none()
        if kb is None:
            raise SkillNotFoundError(SkillRefType.KNOWLEDGE, ref_id)
        return ResolvedSkill(
            name=kb.name,
            description=kb.description or f"Knowledge base: {kb.name}",
            source=SkillRefType.KNOWLEDGE,
            parameters=None,
            metadata={"collection_name": kb.collection_name, "search_mode": kb.search_mode},
        )

    async def _resolve_workflow(self, ref_id: str | UUID) -> ResolvedSkill:
        """Resolve a workflow reference by UUID."""
        wf_id = ref_id if isinstance(ref_id, UUID) else UUID(str(ref_id))
        result = await self._db.execute(
            select(WorkflowModel).where(WorkflowModel.id == wf_id, WorkflowModel.deleted.is_(False))
        )
        wf = result.scalar_one_or_none()
        if wf is None:
            raise SkillNotFoundError(SkillRefType.WORKFLOW, ref_id)
        return ResolvedSkill(
            name=wf.name,
            description=f"Workflow: {wf.name} (mode: {wf.execution_mode})",
            source=SkillRefType.WORKFLOW,
            parameters=None,
            metadata={"execution_mode": wf.execution_mode, "current_version": wf.current_version},
        )

    async def _resolve_agent(self, ref_id: str | UUID) -> ResolvedSkill:
        """Resolve an agent reference by UUID."""
        agent_id = ref_id if isinstance(ref_id, UUID) else UUID(str(ref_id))
        result = await self._db.execute(
            select(AgentModel).where(AgentModel.id == agent_id, AgentModel.deleted.is_(False))
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise SkillNotFoundError(SkillRefType.AGENT, ref_id)
        return ResolvedSkill(
            name=agent.name,
            description=agent.persona or f"Agent: {agent.name}",
            source=SkillRefType.AGENT,
            parameters=None,
            metadata={"mode": agent.mode, "risk_level": agent.risk_level},
        )

    async def invoke(
        self,
        ref: SkillRef,
        context: dict[str, Any],
        port: Any | None = None,
    ) -> Any:
        """Invoke a resolved skill via the appropriate execution path.

        Args:
            ref: The skill reference to invoke.
            context: Execution context (arguments, messages, etc.).
            port: EnginePort instance for delegation.

        Returns:
            The skill's execution result.

        Raises:
            SkillNotFoundError: If the ref cannot be resolved.
            ValueError: If the skill type cannot be invoked.
        """
        match ref.ref_type:
            case SkillRefType.TOOL:
                if port is None:
                    raise ValueError("EnginePort required for tool invocation")
                return await port.tool_execute(str(ref.ref_id), context.get("args", {}), context)
            case SkillRefType.KNOWLEDGE:
                if port is None:
                    raise ValueError("EnginePort required for knowledge query")
                kb_id = ref.ref_id if isinstance(ref.ref_id, UUID) else UUID(str(ref.ref_id))
                return await port.knowledge_query(context.get("query", ""), [kb_id])
            case SkillRefType.WORKFLOW:
                if port is None:
                    raise ValueError("EnginePort required for workflow execution")
                wf_id = ref.ref_id if isinstance(ref.ref_id, UUID) else UUID(str(ref.ref_id))
                return await port.workflow_execute(wf_id, context.get("input", {}), context)
            case SkillRefType.AGENT:
                if port is None:
                    raise ValueError("EnginePort required for agent execution")
                agent_id = ref.ref_id if isinstance(ref.ref_id, UUID) else UUID(str(ref.ref_id))
                messages = context.get("messages", [{"role": "user", "content": context.get("task", "")}])
                return await port.agent_execute(agent_id, messages, context.get("channel_snapshot", {}), context)
            case _:
                raise ValueError(f"Cannot invoke skill type: {ref.ref_type}")

    def format_for_llm(self, skills: list[ResolvedSkill]) -> str:
        """Format resolved skills as XML for LLM system prompt injection.

        Args:
            skills: List of resolved skills to format.

        Returns:
            XML string suitable for system prompt injection.
        """
        if not skills:
            return ""

        parts: list[str] = ["<skills>"]
        for skill in skills:
            parts.append(f'  <skill name="{skill.name}" type="{skill.source}">')
            parts.append(f"    <description>{skill.description}</description>")
            if skill.parameters:
                parts.append(f"    <parameters>{skill.parameters}</parameters>")
            if skill.metadata:
                for key, value in skill.metadata.items():
                    if key not in ("instructions",):  # Skip large fields
                        parts.append(f"    <{key}>{value}</{key}>")
            parts.append("  </skill>")
        parts.append("</skills>")
        return "\n".join(parts)
