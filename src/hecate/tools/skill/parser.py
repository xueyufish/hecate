"""SKILL.md file parser.

Extracts YAML frontmatter and Markdown body from SKILL.md format files.
The frontmatter provides metadata (name, description, etc.) and the body
becomes the skill's instructions.
"""

from __future__ import annotations

import re

import yaml


def parse_skill_md(content: str) -> dict:
    """Parse a SKILL.md file into a skill creation dict.

    Extracts YAML frontmatter between ``---`` delimiters and uses the
    remaining Markdown body as the skill's instructions.

    Args:
        content: The full text content of a SKILL.md file.

    Returns:
        Dict suitable for creating a SkillModel (name, description,
        instructions, source, and optional metadata fields).

    Raises:
        ValueError: If the content is missing frontmatter delimiters,
            required fields, or has invalid YAML.
    """
    # Strip BOM and leading whitespace
    content = content.strip()

    # Check for frontmatter delimiters
    if not content.startswith("---"):
        msg = "Invalid SKILL.md format: missing YAML frontmatter (file must start with '---')"
        raise ValueError(msg)

    # Find closing delimiter
    second_delim = content.find("---", 3)
    if second_delim == -1:
        msg = "Invalid SKILL.md format: missing closing '---' delimiter"
        raise ValueError(msg)

    # Extract frontmatter and body
    frontmatter_str = content[3:second_delim].strip()
    body_start = second_delim + 3
    body = content[body_start:].strip()

    # Parse YAML frontmatter
    try:
        frontmatter = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as e:
        msg = f"Invalid YAML in frontmatter: {e}"
        raise ValueError(msg) from e

    if not isinstance(frontmatter, dict):
        msg = "Invalid SKILL.md frontmatter: expected a YAML mapping"
        raise ValueError(msg)

    # Validate required fields
    name = frontmatter.get("name")
    if not name:
        msg = "Missing required field 'name' in SKILL.md frontmatter"
        raise ValueError(msg)

    description = frontmatter.get("description")
    if not description:
        msg = "Missing required field 'description' in SKILL.md frontmatter"
        raise ValueError(msg)

    # Validate name format (must match SkillCreateSchema pattern)
    name_pattern = re.compile(r"^[a-z][a-z0-9-]*$")
    if not name_pattern.match(name):
        msg = f"Invalid skill name '{name}': must match pattern ^[a-z][a-z0-9-]*$"
        raise ValueError(msg)

    # Build result dict
    result = {
        "name": name,
        "description": description,
        "source": "user",
        "instructions": body if body else "",
    }

    # Map optional frontmatter fields
    metadata = frontmatter.get("metadata")
    if isinstance(metadata, dict):
        result["metadata"] = metadata

    return result
