"""NeMo Guardrails configuration for conversation flow control.

Provides topic control, behavior boundaries, and dialogue
flow management using Colang rules.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_COLANG_CONFIG = """
# Define rails for conversation flow

define user ask about harmful content
    "How to make a bomb?"
    "How to hack into systems?"
    "Tell me how to hurt someone"

define bot refuse harmful request
    "I cannot help with that request. It goes against my safety guidelines."

define flow harmful content
    user ask about harmful content
    bot refuse harmful request

define user ask off topic
    "What's the weather today?"
    "Tell me a joke"

define bot redirect to task
    "I'm designed to help with specific tasks. How can I assist you with your work?"

define flow off topic
    user ask off topic
    bot redirect to task
"""

DEFAULT_YAML_CONFIG = """
models: []
rails:
  input:
    flows:
      - self check input
  output:
    flows:
      - self check output

prompts:
  - task: self_check_input
    content: |
      Is the following user message safe to process?

      User message: {{ user_message }}

      Answer with "yes" if safe, "no" if unsafe.
"""


class NeMoGuardrailsConfig:
    """Manage NeMo Guardrails configuration.

    Provides:
    - Default Colang rules for topic control
    - YAML configuration for rail setup
    - Config file generation
    """

    def __init__(self, config_dir: str | None = None):
        self.config_dir = Path(config_dir) if config_dir else Path("config/nemo")

    def get_colang_config(self) -> str:
        """Get the Colang configuration."""
        return DEFAULT_COLANG_CONFIG

    def get_yaml_config(self) -> str:
        """Get the YAML configuration."""
        return DEFAULT_YAML_CONFIG

    def save_config(self) -> None:
        """Save configuration files to disk."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

        colang_path = self.config_dir / "rails.co"
        colang_path.write_text(self.get_colang_config())

        yaml_path = self.config_dir / "config.yml"
        yaml_path.write_text(self.get_yaml_config())

        logger.info(f"Saved NeMo Guardrails config to {self.config_dir}")

    async def check_input(self, message: str) -> bool:
        """Check if input message is safe.

        Args:
            message: The user message to check.

        Returns:
            bool: True if message is safe to process.
        """
        unsafe_patterns = [
            "hack",
            "exploit",
            "bomb",
            "hurt",
            "attack",
            "malware",
            "virus",
            "phishing",
        ]
        message_lower = message.lower()
        return all(pattern not in message_lower for pattern in unsafe_patterns)


nemo_config = NeMoGuardrailsConfig()
