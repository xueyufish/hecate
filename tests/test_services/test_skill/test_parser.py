"""Tests for SKILL.md parser."""

from __future__ import annotations

import pytest

from hecate.services.skill.parser import parse_skill_md


class TestParseSkillMd:
    """Tests for parse_skill_md()."""

    def test_valid_skill_md(self) -> None:
        content = """---
name: code-review
description: Reviews code for quality
metadata:
  author: test
---

# Code Review

Review code for best practices.
"""
        result = parse_skill_md(content)
        assert result["name"] == "code-review"
        assert result["description"] == "Reviews code for quality"
        assert result["source"] == "user"
        assert "Review code" in result["instructions"]
        assert result["metadata"] == {"author": "test"}

    def test_missing_frontmatter_start(self) -> None:
        with pytest.raises(ValueError, match="missing YAML frontmatter"):
            parse_skill_md("name: test\ndescription: test")

    def test_missing_frontmatter_end(self) -> None:
        with pytest.raises(ValueError, match="missing closing"):
            parse_skill_md("---\nname: test\ndescription: test")

    def test_invalid_yaml(self) -> None:
        with pytest.raises(ValueError, match="Invalid YAML"):
            parse_skill_md("---\n: invalid: yaml: {{}}---\nbody")

    def test_missing_name(self) -> None:
        content = "---\ndescription: test\n---\nbody"
        with pytest.raises(ValueError, match="Missing required field 'name'"):
            parse_skill_md(content)

    def test_missing_description(self) -> None:
        content = "---\nname: test-skill\n---\nbody"
        with pytest.raises(ValueError, match="Missing required field 'description'"):
            parse_skill_md(content)

    def test_invalid_name_format(self) -> None:
        content = "---\nname: Invalid_Name\ndescription: test\n---\nbody"
        with pytest.raises(ValueError, match="Invalid skill name"):
            parse_skill_md(content)

    def test_no_body(self) -> None:
        content = "---\nname: test-skill\ndescription: A test\n---"
        result = parse_skill_md(content)
        assert result["instructions"] == ""

    def test_non_dict_frontmatter(self) -> None:
        content = "---\n- item1\n- item2\n---\nbody"
        with pytest.raises(ValueError, match="expected a YAML mapping"):
            parse_skill_md(content)
