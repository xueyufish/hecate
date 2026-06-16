# ADR-004: SKILL.md Format for Skill System

> **Status**: Accepted

## Context

The Skill system is one of Hecate's core differentiators — it enables agents to dynamically load specialized capabilities on demand. The design needed to determine the skill format, discovery mechanism, and loading strategy.

## Decision

Adopt the **SKILL.md format** (compatible with Claude Code) with **multi-source discovery** (system/user/project) and **on-demand loading** with token budget control.

## Rationale

SKILL.md is a proven format — Claude Code uses it in production. By adopting the same format, Hecate gains compatibility with the existing Claude Code skill ecosystem while adding platform-specific extensions (CRUD operations, remote sources, auto-loading).

Multi-source discovery with a clear priority hierarchy (system → user → project) allows organizations to ship baseline skills while users override or extend them at their level.

On-demand loading ensures agents only consume token budget for skills they're actively using — a skill sitting in the library costs nothing until invoked.

## Consequences

- Skills are markdown files with structured metadata
- The skill loader respects token budgets — skills exceeding the limit are truncated or rejected
- Skill CRUD operations are exposed via management API
