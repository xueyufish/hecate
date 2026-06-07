"""CLI configuration management.

Handles reading and writing CLI configuration from ~/.hecate/config.toml.
Supports named profiles for multi-environment workflows.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

# Default config directory
CONFIG_DIR = Path.home() / ".hecate"
CONFIG_FILE = CONFIG_DIR / "config.toml"

# Default values
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_OUTPUT = "table"


# Module-level state for global options (set by main.py callback)
_GLOBAL_STATE: dict[str, object] = {
    "profile": "default",
    "json": False,
}


def set_global_state(profile: str, json_output: bool) -> None:
    """Set global CLI state (called by main.py callback)."""
    _GLOBAL_STATE["profile"] = profile
    _GLOBAL_STATE["json"] = json_output


def get_profile_name() -> str:
    """Get the active profile name from global state."""
    return str(_GLOBAL_STATE.get("profile", "default"))


def get_output_format() -> str:
    """Get the output format from global state."""
    return "json" if _GLOBAL_STATE.get("json", False) else "table"


def ensure_config_dir() -> None:
    """Create the config directory if it doesn't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """Load the full config file. Returns empty dict if file doesn't exist.

    Returns:
        Dict with profile configurations.
    """
    if not CONFIG_FILE.exists():
        return {}

    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def get_profile(profile_name: str = "default") -> dict[str, Any]:
    """Get a specific profile's configuration.

    Args:
        profile_name: Name of the profile to retrieve.

    Returns:
        Dict with base_url, api_key, output, access_token, refresh_token.
    """
    config = load_config()
    profiles = config.get("profiles", {})

    if profile_name in profiles:
        return dict(profiles[profile_name])

    # First run — return defaults
    return {
        "base_url": DEFAULT_BASE_URL,
        "api_key": "",
        "output": DEFAULT_OUTPUT,
        "access_token": "",
        "refresh_token": "",
    }


def set_profile_value(profile_name: str, key: str, value: str) -> None:
    """Write a single value to a profile in the config file.

    Args:
        profile_name: Name of the profile to update.
        key: Configuration key to set.
        value: Value to write.
    """
    ensure_config_dir()

    # Read existing config
    config = load_config()
    if "profiles" not in config:
        config["profiles"] = {}
    if profile_name not in config["profiles"]:
        config["profiles"][profile_name] = {
            "base_url": DEFAULT_BASE_URL,
            "api_key": "",
            "output": DEFAULT_OUTPUT,
        }

    config["profiles"][profile_name][key] = value

    # Write back — minimal TOML serialization (flat key-value per profile)
    _write_config(config)


def mask_value(key: str, value: str) -> str:
    """Mask sensitive values for display.

    Args:
        key: The config key name.
        value: The actual value.

    Returns:
        Masked string if key is sensitive, otherwise the original value.
    """
    sensitive_keys = {"api_key", "access_token", "refresh_token"}
    if key in sensitive_keys and value:
        if len(value) <= 8:
            return "****"
        return f"{value[:4]}...{value[-4:]}"
    return value


def _write_config(config: dict[str, Any]) -> None:
    """Write config dict back to TOML file.

    Args:
        config: The full configuration dictionary.
    """
    ensure_config_dir()

    lines: list[str] = []
    for section_name, section_data in config.items():
        if isinstance(section_data, dict):
            if section_name == "profiles":
                for profile_name, profile_data in section_data.items():
                    lines.append(f"[profiles.{profile_name}]")
                    if isinstance(profile_data, dict):
                        for key, val in profile_data.items():
                            lines.append(f'{key} = "{val}"')
                    lines.append("")
            else:
                lines.append(f"[{section_name}]")
                for key, val in section_data.items():
                    lines.append(f'{key} = "{val}"')
                lines.append("")

    CONFIG_FILE.write_text("\n".join(lines) + "\n")
