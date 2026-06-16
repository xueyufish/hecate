## Context

The agent model stores configuration as JSON fields (model_config, tools, skills, knowledge_base_ids). Workflows are stored as Graph DSL JSON. Memory blocks are separate entities linked to agents.

For export, we need to:
1. Serialize agent config
2. Include workflow Graph DSL (if mode=workflow)
3. Include memory blocks
4. Exclude runtime data (id, workspace_id, timestamps)

For import, we need to:
1. Validate the JSON format
2. Create new agent with exported config
3. Create workflow (if included)
4. Create memory blocks
5. Re-link KBs by name (optional) or skip

## Goals / Non-Goals

**Goals:**
- Export agent config as portable JSON
- Import JSON to create new agent
- Include workflow Graph DSL in export
- Include memory blocks in export
- Frontend export/import buttons

**Non-Goals:**
- Export knowledge base documents (too large)
- Export conversation history
- Cross-workspace import (P3 multi-tenancy)
- Version migration (deferred)

## Decisions

### D1: JSON format with metadata

**Decision**: Export JSON includes `version`, `exported_at`, `agent` (config), `workflow` (optional Graph DSL), `memory_blocks` (list).

**Rationale**: Self-describing format, easy to extend, includes all necessary context.

### D2: Exclude runtime fields

**Decision**: Exclude `id`, `workspace_id`, `created_at`, `updated_at`, `deleted_at` from export.

**Rationale**: Import creates new entities with fresh IDs. Runtime fields are environment-specific.

### D3: KB references by name (optional)

**Decision**: Export includes `knowledge_base_names` alongside `knowledge_base_ids`. On import, match by name if IDs don't exist.

**Rationale**: KB IDs are UUIDs that differ across environments. Names are portable.

### D4: Import creates new agent (not update)

**Decision**: Import always creates a new agent, never updates existing.

**Rationale**: Safer, avoids accidental overwrites. User can delete old agent if needed.

## Risks / Trade-offs

- **[KB mismatch]** → KBs may not exist in target environment. Mitigation: warning on import, skip missing KBs.
- **[Workflow conflicts]** → Workflow names may conflict. Mitigation: append timestamp to name.
- **[Large exports]** → Agents with many memory blocks could be large. Mitigation: memory blocks are small (text only).
