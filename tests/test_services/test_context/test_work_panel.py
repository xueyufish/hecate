"""Unit tests for WorkPanelBuilder."""

from __future__ import annotations

from hecate.services.context.work_panel import WorkPanelBuilder


class TestWorkPanelBuilder:
    """Tests for the WorkPanelBuilder class."""

    def test_build_panel_short_conversation(self) -> None:
        """Test that short conversations are returned unchanged."""
        builder = WorkPanelBuilder(min_turns=3)
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "How are you?"},
        ]
        priorities = ["critical", "high", "critical"]

        panel = builder.build_panel(messages, priorities)
        assert panel == messages

    def test_build_panel_long_conversation(self) -> None:
        """Test that long conversations are restructured."""
        builder = WorkPanelBuilder(min_turns=2, recent_exchanges=2)
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "First question about Python"},
            {"role": "assistant", "content": "Python is great for..."},
            {"role": "user", "content": "Second question about data"},
            {"role": "assistant", "content": "Data science involves..."},
            {"role": "user", "content": "Third question about ML"},
            {"role": "assistant", "content": "Machine learning is..."},
        ]
        priorities = ["critical", "medium", "medium", "medium", "high", "high", "critical"]

        panel = builder.build_panel(messages, priorities)

        # Should have fewer messages than original
        assert len(panel) <= len(messages)

        # Should preserve system message
        system_msgs = [m for m in panel if m.get("role") == "system"]
        assert len(system_msgs) >= 1

        # Should include recent messages
        user_msgs = [m for m in panel if m.get("role") == "user"]
        assert any("Third question" in m.get("content", "") for m in user_msgs)

    def test_build_panel_preserves_system_messages(self) -> None:
        """Test that system messages are always preserved."""
        builder = WorkPanelBuilder(min_turns=2, recent_exchanges=1)
        messages = [
            {"role": "system", "content": "System instruction 1"},
            {"role": "system", "content": "System instruction 2"},
            {"role": "user", "content": "Question 1"},
            {"role": "assistant", "content": "Answer 1"},
            {"role": "user", "content": "Question 2"},
            {"role": "assistant", "content": "Answer 2"},
        ]
        priorities = ["critical", "critical", "medium", "medium", "high", "high"]

        panel = builder.build_panel(messages, priorities)

        system_msgs = [m for m in panel if m.get("role") == "system"]
        assert len(system_msgs) == 2

    def test_build_panel_adds_objective_summary(self) -> None:
        """Test that objective summary is added for long conversations."""
        builder = WorkPanelBuilder(min_turns=1, recent_exchanges=1)
        messages = [
            {"role": "user", "content": "Help me build a REST API"},
            {"role": "assistant", "content": "Sure, let's start..."},
            {"role": "user", "content": "Now add authentication"},
            {"role": "assistant", "content": "Adding auth..."},
        ]
        priorities = ["critical", "medium", "high", "high"]

        panel = builder.build_panel(messages, priorities)

        objective_msgs = [m for m in panel if "[Original objective]" in m.get("content", "")]
        assert len(objective_msgs) >= 1
        assert "REST API" in objective_msgs[0]["content"]

    def test_build_panel_adds_context_summary(self) -> None:
        """Test that context summary is added for older messages."""
        builder = WorkPanelBuilder(min_turns=1, recent_exchanges=1)
        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
        ]
        priorities = ["medium", "medium", "high", "high"]

        panel = builder.build_panel(messages, priorities)

        summary_msgs = [m for m in panel if "[Previous context summary]" in m.get("content", "")]
        assert len(summary_msgs) >= 1

    def test_should_build_panel_true_for_long(self) -> None:
        """Test should_build_panel returns True for long conversations."""
        builder = WorkPanelBuilder(min_turns=2)
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "Q3"},
        ]

        assert builder.should_build_panel(messages) is True

    def test_should_build_panel_false_for_short(self) -> None:
        """Test should_build_panel returns False for short conversations."""
        builder = WorkPanelBuilder(min_turns=3)
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]

        assert builder.should_build_panel(messages) is False

    def test_build_panel_empty_messages(self) -> None:
        """Test build_panel with empty messages."""
        builder = WorkPanelBuilder()
        panel = builder.build_panel([], [])
        assert panel == []

    def test_extract_objective(self) -> None:
        """Test objective extraction from first user message."""
        builder = WorkPanelBuilder()
        messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Build a web app"},
            {"role": "assistant", "content": "Sure"},
        ]

        objective = builder._extract_objective(messages)
        assert "Build a web app" in objective

    def test_extract_objective_no_user_message(self) -> None:
        """Test objective extraction when no user message exists."""
        builder = WorkPanelBuilder()
        messages = [
            {"role": "system", "content": "System"},
        ]

        objective = builder._extract_objective(messages)
        assert objective == ""

    def test_find_recent_boundary(self) -> None:
        """Test finding the boundary for recent messages."""
        builder = WorkPanelBuilder(recent_exchanges=2)
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
            {"role": "user", "content": "Q3"},
            {"role": "assistant", "content": "A3"},
        ]

        boundary = builder._find_recent_boundary(messages)
        # Should find Q2 as the start of recent exchanges
        assert boundary <= 2

    def test_summarize_older_messages(self) -> None:
        """Test summarization of older messages."""
        builder = WorkPanelBuilder()
        messages = [
            {"role": "user", "content": "Tell me about Python"},
            {"role": "assistant", "content": "Python is a programming language."},
            {"role": "user", "content": "What about Java?"},
        ]

        summary = builder._summarize_older_messages(messages)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_summarize_empty_messages(self) -> None:
        """Test summarization of empty message list."""
        builder = WorkPanelBuilder()
        summary = builder._summarize_older_messages([])
        assert summary == ""
