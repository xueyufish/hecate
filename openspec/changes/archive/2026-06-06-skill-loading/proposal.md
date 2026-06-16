## Why

Feature 5.9 is the last remaining P1 gap (18/19 complete). SkillModel and a read-only API already exist, but skills are never loaded or injected into agent execution. The `AgentModel.skills` field stores an empty list and no code reads it. Without this change, agents cannot be extended with reusable instruction sets — the core value proposition of the skill system.

## What Changes

- Add `workspace_id` to `SkillModel` for multi-tenant isolation (currently missing; all other resource models have it). Change unique index from `(name)` to `(workspace_id, name)`. System skills use zero UUID.
- Create `SkillLoader` service that loads skill instructions from DB by agent's skill names, formats them as XML-tagged context blocks, and handles `auto_load` + `max_tokens` budget.
- Wire skill loading into `WorkflowExecutionService.execute()` and `AgentExecutionPort.agent_execute()` to inject activated skill instructions into the system prompt using XML format: `<skills><skill name="...">body</skill></skills>`.
- Add CRUD endpoints: `POST /api/skills`, `PUT /api/skills/{id}`, `DELETE /api/skills/{id}`.
- Add `POST /api/skills/import` endpoint that parses SKILL.md files (YAML frontmatter + Markdown body) and creates SkillModel records.
- Add `POST /api/agents/{id}/skills` and `DELETE /api/agents/{id}/skills/{skill_name}` for managing agent-skill associations.

## Capabilities

### New Capabilities
- `skill-loader`: Skill loading service — resolves agent skill names to instructions, formats context, handles auto_load and token budgets
- `skill-api`: Full skill CRUD + import API — create, read, update, delete skills; import SKILL.md files

### Modified Capabilities
- `engine-ports`: `context_assemble()` and execution flow modified to inject skill instructions into system prompt
- `data-models`: SkillModel gains `workspace_id` field and updated unique index

## Impact

- **Models**: `src/hecate/models/skill.py` — add `workspace_id`, change index, update schemas
- **New service**: `src/hecate/services/skill/loader.py` — SkillLoader class
- **Services**: `src/hecate/services/workflow/execution_service.py` — load skills in `execute()`
- **Services**: `src/hecate/services/orchestration/agent_execution_port.py` — load skills for sub-agents
- **API**: `src/hecate/api/management/skills.py` — add POST, PUT, DELETE, import endpoints
- **API**: `src/hecate/api/management/agents.py` — add skill association endpoints
- **Tests**: `tests/test_services/test_skill/`, `tests/test_api/test_skills.py`
