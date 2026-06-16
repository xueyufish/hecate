"""Unit tests for BudgetManager."""

from __future__ import annotations

from uuid import uuid4

from hecate.services.context.budget import (
    BudgetAllocation,
    BudgetCheck,
    BudgetManager,
    DegradationLevel,
    MessagePriority,
)


class TestBudgetManager:
    """Tests for the BudgetManager class."""

    def test_allocate_budget(self) -> None:
        """Test budget allocation for a session."""
        manager = BudgetManager()
        session_id = uuid4()
        allocation = manager.allocate(session_id, "gpt-4o")

        assert allocation.session_id == session_id
        assert allocation.total_budget > 0
        assert allocation.tokens_used == 0
        assert allocation.tokens_remaining == allocation.total_budget

    def test_allocate_custom_budget(self) -> None:
        """Test custom budget allocation."""
        manager = BudgetManager()
        session_id = uuid4()
        allocation = manager.allocate(session_id, "gpt-4o", custom_budget=5000)

        assert allocation.total_budget == 5000

    def test_get_allocation_existing(self) -> None:
        """Test getting existing allocation."""
        manager = BudgetManager()
        session_id = uuid4()
        manager.allocate(session_id, "gpt-4o")

        allocation = manager.get_allocation(session_id)
        assert allocation is not None
        assert allocation.session_id == session_id

    def test_get_allocation_nonexistent(self) -> None:
        """Test getting non-existent allocation returns None."""
        manager = BudgetManager()
        allocation = manager.get_allocation(uuid4())
        assert allocation is None

    def test_check_budget_within_limit(self) -> None:
        """Test budget check when within limits."""
        manager = BudgetManager()
        session_id = uuid4()
        manager.allocate(session_id, "gpt-4o", custom_budget=10000)

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        check = manager.check_budget(session_id, messages, "gpt-4o")
        assert check.within_budget is True
        assert check.deficit == 0

    def test_check_budget_exceeded(self) -> None:
        """Test budget check when exceeded."""
        manager = BudgetManager()
        session_id = uuid4()
        manager.allocate(session_id, "gpt-4o", custom_budget=10)  # Very small budget

        # Create messages that will exceed 10 tokens
        messages = [
            {"role": "user", "content": "This is a longer message that should exceed the tiny budget"},
            {"role": "assistant", "content": "And this response adds more tokens to the count"},
        ]

        check = manager.check_budget(session_id, messages, "gpt-4o")
        assert check.within_budget is False
        assert check.deficit > 0

    def test_update_usage(self) -> None:
        """Test updating token usage."""
        manager = BudgetManager()
        session_id = uuid4()
        manager.allocate(session_id, "gpt-4o")

        manager.update_usage(session_id, 500)
        allocation = manager.get_allocation(session_id)
        assert allocation is not None
        assert allocation.tokens_used == 500

    def test_record_degradation(self) -> None:
        """Test recording degradation events."""
        manager = BudgetManager()
        session_id = uuid4()
        manager.allocate(session_id, "gpt-4o")

        manager.record_degradation(session_id, DegradationLevel.DROP)
        manager.record_degradation(session_id, DegradationLevel.COMPRESS)

        allocation = manager.get_allocation(session_id)
        assert allocation is not None
        assert len(allocation.degradation_events) == 2

    def test_get_usage_report(self) -> None:
        """Test getting usage report."""
        manager = BudgetManager()
        session_id = uuid4()
        manager.allocate(session_id, "gpt-4o", custom_budget=1000)
        manager.update_usage(session_id, 500)

        report = manager.get_usage_report(session_id)
        assert report["allocated"] == 1000
        assert report["used"] == 500
        assert report["remaining"] == 500
        assert report["utilization"] == 0.5

    def test_get_usage_report_nonexistent(self) -> None:
        """Test getting usage report for non-existent session."""
        manager = BudgetManager()
        report = manager.get_usage_report(uuid4())
        assert report["allocated"] == 0
        assert report["used"] == 0


class TestBudgetCheck:
    """Tests for the BudgetCheck dataclass."""

    def test_within_budget(self) -> None:
        """Test BudgetCheck when within budget."""
        check = BudgetCheck(
            within_budget=True,
            total_tokens=500,
            budget=1000,
            deficit=0,
        )
        assert check.within_budget is True
        assert check.deficit == 0

    def test_exceeded_budget(self) -> None:
        """Test BudgetCheck when budget exceeded."""
        check = BudgetCheck(
            within_budget=False,
            total_tokens=1500,
            budget=1000,
            deficit=500,
        )
        assert check.within_budget is False
        assert check.deficit == 500


class TestBudgetAllocation:
    """Tests for the BudgetAllocation dataclass."""

    def test_tokens_remaining(self) -> None:
        """Test tokens remaining calculation."""
        allocation = BudgetAllocation(
            session_id=uuid4(),
            total_budget=1000,
            tokens_used=300,
        )
        assert allocation.tokens_remaining == 700

    def test_tokens_remaining_negative_clamped(self) -> None:
        """Test tokens remaining is clamped to 0."""
        allocation = BudgetAllocation(
            session_id=uuid4(),
            total_budget=1000,
            tokens_used=1500,
        )
        assert allocation.tokens_remaining == 0


class TestMessagePriority:
    """Tests for MessagePriority enum."""

    def test_priority_values(self) -> None:
        """Test priority enum values."""
        assert MessagePriority.CRITICAL.value == "critical"
        assert MessagePriority.HIGH.value == "high"
        assert MessagePriority.MEDIUM.value == "medium"
        assert MessagePriority.LOW.value == "low"


class TestDegradationLevel:
    """Tests for DegradationLevel enum."""

    def test_degradation_values(self) -> None:
        """Test degradation level enum values."""
        assert DegradationLevel.NONE.value == "none"
        assert DegradationLevel.DROP.value == "drop"
        assert DegradationLevel.COMPRESS.value == "compress"
        assert DegradationLevel.EMERGENCY.value == "emergency"
