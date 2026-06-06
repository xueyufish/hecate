## 1. Data Model Migration

- [x] 1.1 Add `workspace_id` column to `SkillModel` in `src/hecate/models/skill.py` — UUID with default `00000000-0000-0000-0000-000000000000`
- [x] 1.2 Change `idx_skills_name` unique index to composite `(workspace_id, name)` with `postgresql_where=deleted_at.is_(None)`
- [x] 1.3 Update `SkillCreateSchema` to exclude `workspace_id` from request body (set from auth context)
- [x] 1.4 Update `SkillReadSchema` to include `workspace_id`
- [x] 1.5 Create Alembic migration: add column with default zero UUID, drop old index, create new composite index

## 2. SkillLoader Service

- [x] 2.1 Create `src/hecate/services/skill/__init__.py` (empty package marker)
- [x] 2.2 Create `src/hecate/services/skill/loader.py` with `SkillLoader` class accepting `db: AsyncSession`
- [x] 2.3 Implement `format_skills(agent_id: UUID, workspace_id: UUID) -> str` — query agent by ID, read `skills` list, load `SkillModel` records by name + workspace, format as XML `<skills>` block
- [x] 2.4 Handle missing skills gracefully — log warning, skip, continue with remaining
- [x] 2.5 Implement `auto_load` inclusion — query all skills with `auto_load=True` in workspace, merge with agent's explicit skills, deduplicate by name
- [x] 2.6 Implement `max_tokens` truncation — estimate token count (len/4), truncate individual skills to their `max_tokens`, drop lowest-priority skills if total exceeds 4000 token budget
- [x] 2.7 Format output: each skill as `<skill name="...">description\n\ninstructions</skill>`, wrapped in `<skills>...</skills>` tags

## 3. Wire Skill Loading into Execution

- [x] 3.1 Modify `WorkflowExecutionService.execute()` — when `agent_id` is provided, load agent by ID, call `SkillLoader.format_skills()`, construct `system_prompt = persona + skills_block`
- [x] 3.2 Modify `AgentExecutionPort.agent_execute()` — load skills via `SkillLoader`, append to system message alongside persona
- [x] 3.3 Update `_process_chat()` in `src/hecate/api/v1/chat.py` — when using enhanced path with agent_id, pass agent_id to `execute()` so skills can be loaded
- [x] 3.4 Handle `persona=None` fallback — use "You are a helpful assistant." as base when persona is not set

## 4. Skill CRUD API

- [x] 4.1 Add `POST /api/skills` endpoint — create skill with `workspace_id` from auth context
- [x] 4.2 Add `PUT /api/skills/{id}` endpoint — update skill fields, verify workspace ownership
- [x] 4.3 Add `DELETE /api/skills/{id}` endpoint — soft-delete, verify workspace ownership
- [x] 4.4 Update `GET /api/skills` — filter by `workspace_id` from auth context, also include system skills (`workspace_id=00000000`)
- [x] 4.5 Add workspace ownership check helper — reject operations on skills from other workspaces

## 5. SKILL.md Import API

- [x] 5.1 Create `src/hecate/services/skill/parser.py` with `parse_skill_md(content: str) -> dict` — extract YAML frontmatter between `---` delimiters, parse with `yaml.safe_load()`, use remaining text as instructions
- [x] 5.2 Validate parsed frontmatter — require `name` and `description`, apply SkillCreateSchema validation rules to name format (`^[a-z][a-z0-9-]*$`)
- [x] 5.3 Add `POST /api/skills/import` endpoint — accept `UploadFile`, parse with `parse_skill_md()`, create SkillModel with `source="user"`, return 201
- [x] 5.4 Handle edge cases: file too large (limit to 100KB), invalid YAML, missing delimiters, non-UTF8 encoding

## 6. Agent-Skill Association API

- [x] 6.1 Add `POST /api/agents/{id}/skills` endpoint — accept `{"skill_name": "..."}`, append to agent's `skills` list (idempotent, no duplicates)
- [x] 6.2 Add `DELETE /api/agents/{id}/skills/{skill_name}` endpoint — remove skill name from agent's `skills` list (idempotent)
- [x] 6.3 Validate skill exists in workspace before adding association — return 404 if skill name not found

## 7. Tests

- [x] 7.1 Create `tests/test_services/test_skill/__init__.py`
- [x] 7.2 Create `tests/test_services/test_skill/test_loader.py` — test format_skills with skills, no skills, missing skills, auto_load, token truncation, XML format
- [x] 7.3 Create `tests/test_services/test_skill/test_parser.py` — test parse_skill_md with valid SKILL.md, missing frontmatter, invalid YAML, missing required fields
- [x] 7.4 Create `tests/test_api/test_skills.py` — test POST/PUT/DELETE/import endpoints, workspace isolation, duplicate name, agent-skill association
- [x] 7.5 Test workspace isolation — verify skills from workspace A are not visible to workspace B
- [x] 7.6 Run `python -m pytest tests/ -q` — all pass, no regressions

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/`
- [x] 8.2 Run `ruff format --check src/ tests/`
- [x] 8.3 Run `mypy src/`
- [x] 8.4 Run `python -m pytest tests/ -q` — no regressions
