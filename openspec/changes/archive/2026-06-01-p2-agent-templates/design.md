## Context

The orchestration template system already exists with file-based JSON templates in `src/hecate/data/orchestration_templates/`. It provides:
- `GET /api/orchestration-templates` — list with metadata
- `GET /api/orchestration-templates/{id}` — full Graph DSL JSON
- Templates loaded once and cached
- Metadata includes: name, description, category, node/edge counts

Agent templates follow the same pattern but for agent configurations instead of workflows.

## Goals / Non-Goals

**Goals:**
- File-based JSON templates (same pattern as orchestration templates)
- 5 built-in templates covering common use cases
- Template instantiation creates a new agent via existing API
- Frontend template picker on agent creation page
- Template preview showing configuration summary

**Non-Goals:**
- User-created templates (deferred — would need database storage)
- Template versioning (deferred)
- Template sharing/export (deferred)

## Decisions

### D1: File-based JSON storage (not database)

**Decision**: Store templates as JSON files in `src/hecate/data/agent_templates/`.

**Rationale**: Same pattern as orchestration templates. Built-in templates don't need user modification. Simple, no migration needed.

### D2: Template instantiation via existing API

**Decision**: `POST /api/agent-templates/{id}/instantiate` returns the template config, frontend uses it to pre-fill the form, then submits via existing `POST /api/agents`.

**Rationale**: Reuses existing agent creation logic, validation, and KB ID validation. No new database operations needed.

### D3: Template schema mirrors AgentCreateSchema

**Decision**: Template JSON structure matches `AgentCreateSchema` fields plus metadata (name, description, category, preview).

**Rationale**: Direct mapping, no transformation needed during instantiation.

## Risks / Trade-offs

- **[No user templates]** → Only built-in templates. Mitigation: easy to extend later with database storage.
- **[KB IDs in templates]** → Templates reference KB IDs that may not exist. Mitigation: instantiation validates KB IDs.
- **[Model availability]** → Templates reference specific models. Mitigation: frontend shows warning if model unavailable.
