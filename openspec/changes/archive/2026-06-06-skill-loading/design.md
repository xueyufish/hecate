## Context

Feature 5.9 is the last P1 gap. The current state:

- `SkillModel` ORM exists with full field set (name, description, source, instructions, allowed_tools, metadata, scripts, references, max_tokens, auto_load) but **no `workspace_id`** — the only resource model without multi-tenant isolation.
- `AgentModel.skills` is a JSON list field, always empty, never read by any code.
- Read-only API exists: `GET /api/skills`, `GET /api/skills/{id}`.
- No skill loading, injection, or activation mechanism exists.
- The execution pipeline has a clear system prompt injection point: `WorkflowExecutionService.execute(system_prompt=...)` and `AgentExecutionPort.agent_execute()` (which already uses `agent.persona`).

The feature catalog references SKILL.md format, multi-source discovery, Plan Agent auto-selection, and Skill Play — all P2+ features. P1 scope is limited to DB-based skill loading and system prompt injection.

## Goals / Non-Goals

**Goals:**
- Complete the last P1 gap: agents can be extended with reusable skill instructions
- Multi-tenant skill isolation via `workspace_id` (consistent with AgentModel, ToolModel, etc.)
- Skill instructions injected into agent system prompt using industry-standard XML format
- Full CRUD API for skill management
- SKILL.md import endpoint for parsing YAML frontmatter + Markdown body
- Agent-skill association management via API

**Non-Goals:**
- Filesystem-based skill discovery (scanning directories for SKILL.md files) — P2
- Remote skill registry / marketplace — P2
- Plan Agent auto-selection of skills based on task analysis — P2
- Skill Play consultation mode — P2
- Progressive disclosure Level 3 (on-demand resource loading) — P2
- Event emission for skill lifecycle (SkillLoadedEvent, etc.) — P2

## Decisions

### D1: SkillModel adds workspace_id

**Decision**: Add `workspace_id` column to `SkillModel`, matching AgentModel/ToolModel/KnowledgeBaseModel pattern. System skills use zero UUID `00000000-0000-0000-0000-000000000000`. Unique index changes from `(name)` to `(workspace_id, name)`.

**Rationale**: All other resource models have `workspace_id`. Without it, skills are globally shared across tenants — a data isolation violation. The `source` field (`system`/`user`/`project`) naturally maps to workspace scoping.

**Alternative considered**: Keep skills global, use agent-skill associations for isolation. Rejected because: (1) list API would leak cross-tenant skill names, (2) inconsistent with every other model, (3) P3 multi-tenancy migration would be disruptive.

### D2: Agent.skills stores skill name strings

**Decision**: `AgentModel.skills` stores a list of skill name strings (e.g., `["code-review", "unit-test"]`), matched against `SkillModel.name`.

**Rationale**: Skill names are unique within a workspace (via index). Names are human-readable, debuggable, and match the SKILL.md filename convention. UUIDs would require an extra join for no benefit.

**Alternative considered**: Store skill UUIDs. Rejected because: (1) less readable in API responses and DB queries, (2) skill names are already unique per workspace, (3) CrewAI and Claude Code both use name-based references.

### D3: XML-tagged format in single system message

**Decision**: Skill instructions injected into system prompt using XML format within a single system message:

```
{persona}

<skills>
<skill name="code-review">
{description}

{instructions}
</skill>
</skills>
```

**Rationale**: Industry consensus — CrewAI, Claude Code, DeerFlow (ByteDance), GPTMe, IronClaw, OpenDerisk, and DeepResearchAgent all use `<skill name="...">...</skill>` XML tags. Single system message avoids engine-layer changes (Hecate's `system_prompt` is a string passed to `build_chat_graph()`).

**Alternative considered**: One SystemMessage per skill (DeerFlow pattern). Rejected because it requires changing engine-layer message handling.

### D4: SkillLoader as standalone service

**Decision**: Create `SkillLoader` class in `services/skill/loader.py` responsible for:
1. Loading skills by agent ID (query agent.skills → query SkillModel by name + workspace)
2. Formatting instructions as XML context block
3. Respecting `auto_load` flag and `max_tokens` budget
4. Returning formatted string for system prompt injection

**Rationale**: Separation of concerns — loading logic is independent of injection point. Both `WorkflowExecutionService` and `AgentExecutionPort` can use the same loader. Independently testable.

**Alternative considered**: Inline loading in `execute()`. Rejected because: duplicated logic between two injection points, harder to test.

### D5: System prompt construction

**Decision**: `WorkflowExecutionService.execute()` will:
1. Load agent by ID if `agent_id` is provided
2. Call `SkillLoader.format_skills(agent_id, workspace_id)` to get XML block
3. Construct `system_prompt = persona + "\n\n" + skills_block`
4. Pass to `build_chat_graph(system_prompt=...)`

For `AgentExecutionPort.agent_execute()`:
1. Already loads agent by ID
2. Call same `SkillLoader.format_skills()`
3. Replace `system_message = {"role": "system", "content": persona}` with persona + skills

### D6: SKILL.md import format

**Decision**: Import endpoint accepts multipart form with SKILL.md file content. Parser extracts:
- YAML frontmatter (between `---` delimiters) → name, description, metadata
- Markdown body (after frontmatter) → instructions

Maps to existing SkillModel fields. If frontmatter lacks required fields, use defaults or raise validation error.

## Risks / Trade-offs

**[Risk] System prompt token overflow** → Multiple skills with large instructions could exceed model context window. Mitigation: `max_tokens` field on SkillModel for per-skill budget; SkillLoader truncates total skills block to budget. P1 uses simple truncation, P2 can add smarter compression.

**[Risk] Breaking change for existing skills table** → Adding `workspace_id` requires migration. Mitigation: Migration adds column with default zero UUID (matching current behavior), updates index. Existing data is unaffected.

**[Risk] Name collision after workspace_id addition** → Existing `idx_skills_name` is globally unique. After adding `workspace_id`, same name can exist in different workspaces. Migration drops old index, creates new composite index. Zero-downtime since existing rows all get zero UUID.

**[Trade-off] P1 scope excludes filesystem discovery** → Users must create skills via API or import endpoint, not by dropping SKILL.md files into a directory. This is acceptable for P1 (enterprise users manage via API). P2 adds filesystem watching.
