"""Unit tests for DynamicTaskAllocator."""

from __future__ import annotations

from hecate.services.multi_agent.task_allocator import (
    AgentInfo,
    DynamicTaskAllocator,
)


class TestDynamicTaskAllocator:
    """Tests for the DynamicTaskAllocator class."""

    def test_register_agent(self) -> None:
        """Test registering an agent."""
        allocator = DynamicTaskAllocator()
        agent = AgentInfo(
            agent_id="agent-1",
            name="Agent 1",
            capabilities=["search", "read"],
        )

        allocator.register_agent(agent)

        assert "agent-1" in allocator._agents

    def test_allocate_basic(self) -> None:
        """Test basic task allocation."""
        allocator = DynamicTaskAllocator()
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-1",
                name="Agent 1",
                capabilities=["search"],
            )
        )

        result = allocator.allocate("Search for Python tutorials")

        assert result is not None
        assert result.agent_id == "agent-1"

    def test_allocate_with_capabilities(self) -> None:
        """Test allocation with required capabilities."""
        allocator = DynamicTaskAllocator()
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-1",
                name="Agent 1",
                capabilities=["search"],
            )
        )
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-2",
                name="Agent 2",
                capabilities=["write", "execute"],
            )
        )

        result = allocator.allocate(
            "Write a Python script",
            required_capabilities=["write"],
        )

        assert result is not None
        assert result.agent_id == "agent-2"

    def test_allocate_load_balancing(self) -> None:
        """Test load-aware allocation."""
        allocator = DynamicTaskAllocator()
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-1",
                name="Agent 1",
                capabilities=["search"],
                current_load=5,
            )
        )
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-2",
                name="Agent 2",
                capabilities=["search"],
                current_load=2,
            )
        )

        result = allocator.allocate("Search task")

        assert result is not None
        assert result.agent_id == "agent-2"

    def test_allocate_no_candidates(self) -> None:
        """Test allocation when no agents available."""
        allocator = DynamicTaskAllocator()
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-1",
                name="Agent 1",
                capabilities=["search"],
                current_load=10,
                max_load=10,
            )
        )

        result = allocator.allocate("Search task")

        assert result is None

    def test_update_load(self) -> None:
        """Test updating agent load."""
        allocator = DynamicTaskAllocator()
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-1",
                name="Agent 1",
                capabilities=[],
            )
        )

        allocator.update_load("agent-1", 5)

        assert allocator.get_agent_load("agent-1") == 5

    def test_get_available_agents(self) -> None:
        """Test getting available agents."""
        allocator = DynamicTaskAllocator()
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-1",
                name="Agent 1",
                capabilities=[],
                current_load=5,
                max_load=10,
            )
        )
        allocator.register_agent(
            AgentInfo(
                agent_id="agent-2",
                name="Agent 2",
                capabilities=[],
                current_load=10,
                max_load=10,
            )
        )

        available = allocator.get_available_agents()

        assert len(available) == 1
        assert available[0].agent_id == "agent-1"
