"""Structured JSON logger for machine-parseable logs.

Provides structured logging with context enrichment (session_id, agent_id,
user_id) for integration with ELK stack and other log aggregation systems.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class StructuredLogger:
    """Structured JSON logger with context enrichment.

    Outputs logs in JSON format with:
    - Timestamp (ISO 8601)
    - Log level
    - Message
    - Context fields (session_id, agent_id, user_id)
    - Custom metadata
    """

    def __init__(
        self,
        name: str,
        level: int = logging.INFO,
    ) -> None:
        """Initialize the structured logger.

        Args:
            name: Logger name (usually module name).
            level: Log level.
        """
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._context: dict[str, Any] = {}

        # Add JSON handler if not already present
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def set_context(
        self,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Set persistent context fields for all subsequent logs.

        Args:
            session_id: Session identifier.
            agent_id: Agent identifier.
            user_id: User identifier.
            **kwargs: Additional context fields.
        """
        if session_id is not None:
            self._context["session_id"] = session_id
        if agent_id is not None:
            self._context["agent_id"] = agent_id
        if user_id is not None:
            self._context["user_id"] = user_id
        self._context.update(kwargs)

    def _format_message(
        self,
        level: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Format a log message as JSON.

        Args:
            level: Log level string.
            message: Log message.
            extra: Additional fields.

        Returns:
            JSON-formatted log string.
        """
        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
            **self._context,
        }

        if extra:
            log_entry.update(extra)

        return json.dumps(log_entry, default=str)

    def info(
        self,
        message: str,
        **kwargs: Any,
    ) -> None:
        """Log an info message.

        Args:
            message: Log message.
            **kwargs: Additional fields.
        """
        formatted = self._format_message("INFO", message, kwargs)
        self._logger.info(formatted)

    def warning(
        self,
        message: str,
        **kwargs: Any,
    ) -> None:
        """Log a warning message.

        Args:
            message: Log message.
            **kwargs: Additional fields.
        """
        formatted = self._format_message("WARNING", message, kwargs)
        self._logger.warning(formatted)

    def error(
        self,
        message: str,
        **kwargs: Any,
    ) -> None:
        """Log an error message.

        Args:
            message: Log message.
            **kwargs: Additional fields.
        """
        formatted = self._format_message("ERROR", message, kwargs)
        self._logger.error(formatted)

    def debug(
        self,
        message: str,
        **kwargs: Any,
    ) -> None:
        """Log a debug message.

        Args:
            message: Log message.
            **kwargs: Additional fields.
        """
        formatted = self._format_message("DEBUG", message, kwargs)
        self._logger.debug(formatted)
