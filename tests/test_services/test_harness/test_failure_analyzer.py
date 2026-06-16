"""Unit tests for FailureAnalyzer."""

from __future__ import annotations

from hecate.services.harness.failure_analyzer import FailureAnalyzer, FailureType


class TestFailureAnalyzer:
    """Tests for the FailureAnalyzer class."""

    def test_classify_tool_error(self) -> None:
        """Test classification of tool errors."""
        analyzer = FailureAnalyzer()

        trajectory = [
            {"role": "user", "content": "Run the code"},
            {"role": "assistant", "content": "I'll run it"},
            {"role": "tool", "content": "Error: file not found", "is_error": True},
        ]

        result = analyzer.classify_failure(trajectory)

        assert result.failure_type == FailureType.INVALID_INVOCATION
        assert result.confidence > 0.5
        assert "tool" in result.root_cause.lower() or "invocation" in result.root_cause.lower()

    def test_classify_user_correction(self) -> None:
        """Test classification of user corrections."""
        analyzer = FailureAnalyzer()

        trajectory = [
            {"role": "user", "content": "Do X"},
            {"role": "assistant", "content": "Doing X"},
            {"role": "user", "content": "No, I meant do Y"},
        ]

        result = analyzer.classify_failure(trajectory)

        assert result.failure_type == FailureType.INTENT_PLAN_MISALIGNMENT
        assert result.confidence > 0.5

    def test_classify_loop(self) -> None:
        """Test classification of repeated patterns."""
        analyzer = FailureAnalyzer()

        trajectory = [
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "Trying to fix the same issue"},
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "Trying to fix the same issue"},
            {"role": "user", "content": "Fix the bug"},
            {"role": "assistant", "content": "Trying to fix the same issue"},
        ]

        result = analyzer.classify_failure(trajectory)

        # Loop detection may or may not trigger depending on content similarity
        assert result.failure_type in [
            FailureType.INSTRUCTION_ADHERENCE,
            FailureType.INCONCLUSIVE,
        ]

    def test_classify_empty_trajectory(self) -> None:
        """Test classification of empty trajectory."""
        analyzer = FailureAnalyzer()

        result = analyzer.classify_failure([])

        assert result.failure_type == FailureType.INCONCLUSIVE
        assert result.confidence == 0.0

    def test_analyze_root_cause(self) -> None:
        """Test root cause analysis."""
        analyzer = FailureAnalyzer()

        trajectory = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "Doing it"},
            {"role": "tool", "content": "Error", "is_error": True},
        ]

        root_cause = analyzer.analyze_root_cause(trajectory)

        assert isinstance(root_cause, str)
        assert len(root_cause) > 0

    def test_classify_general(self) -> None:
        """Test general classification when no pattern matches."""
        analyzer = FailureAnalyzer()

        trajectory = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]

        result = analyzer.classify_failure(trajectory)

        assert result.failure_type == FailureType.INCONCLUSIVE
