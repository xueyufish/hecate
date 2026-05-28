"""Unit tests for StructuredLogger."""

from __future__ import annotations

import json
import logging
from io import StringIO

from hecate.services.observability.structured_logger import StructuredLogger


class TestStructuredLogger:
    """Tests for the StructuredLogger class."""

    def _get_logger_output(self, logger: StructuredLogger) -> str:
        """Helper to capture logger output."""
        # Get the internal logger
        internal_logger = logger._logger

        # Create a string handler to capture output
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        internal_logger.addHandler(handler)

        return stream, handler

    def test_info_message(self) -> None:
        """Test info level logging."""
        logger = StructuredLogger("test_info")
        stream, handler = self._get_logger_output(logger)

        logger.info("Test message")
        handler.flush()

        output = stream.getvalue().strip()
        parsed = json.loads(output)

        assert parsed["level"] == "INFO"
        assert parsed["message"] == "Test message"
        assert "timestamp" in parsed

    def test_context_enrichment(self) -> None:
        """Test context enrichment in logs."""
        logger = StructuredLogger("test_context")
        stream, handler = self._get_logger_output(logger)

        logger.set_context(
            session_id="sess-123",
            agent_id="agent-456",
            user_id="user-789",
        )
        logger.info("Test with context")
        handler.flush()

        output = stream.getvalue().strip()
        parsed = json.loads(output)

        assert parsed["session_id"] == "sess-123"
        assert parsed["agent_id"] == "agent-456"
        assert parsed["user_id"] == "user-789"

    def test_extra_fields(self) -> None:
        """Test extra fields in log messages."""
        logger = StructuredLogger("test_extra")
        stream, handler = self._get_logger_output(logger)

        logger.info("Test with extra", custom_field="value", count=42)
        handler.flush()

        output = stream.getvalue().strip()
        parsed = json.loads(output)

        assert parsed["custom_field"] == "value"
        assert parsed["count"] == 42

    def test_json_format(self) -> None:
        """Test that output is valid JSON."""
        logger = StructuredLogger("test_json")
        stream, handler = self._get_logger_output(logger)

        logger.info("JSON test")
        handler.flush()

        output = stream.getvalue().strip()
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_set_context(self) -> None:
        """Test setting context fields."""
        logger = StructuredLogger("test_set")
        logger.set_context(session_id="s1", agent_id="a1")

        assert logger._context["session_id"] == "s1"
        assert logger._context["agent_id"] == "a1"

    def test_format_message(self) -> None:
        """Test message formatting."""
        logger = StructuredLogger("test_format")
        formatted = logger._format_message("INFO", "test msg", {"extra": "data"})

        output = json.loads(formatted)
        assert output["level"] == "INFO"
        assert output["message"] == "test msg"
        assert output["extra"] == "data"
