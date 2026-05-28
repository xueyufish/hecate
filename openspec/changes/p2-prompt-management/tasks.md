## 1. Data Models

- [ ] 1.1 Create `PromptModel` ORM in `models/prompt.py` — fields: id, workspace_id, name, created_at, updated_at, deleted_at
- [ ] 1.2 Create `PromptVersionModel` ORM in `models/prompt.py` — fields: id, prompt_id, version, template(TEXT), variables(JSONB), labels(JSONB), created_at
- [ ] 1.3 Create Pydantic schemas: PromptCreateSchema, PromptUpdateSchema, PromptReadSchema, PromptVersionReadSchema
- [ ] 1.4 Generate Alembic migration for prompts and prompt_versions tables
- [ ] 1.5 Update `alembic/env.py` to import prompt model

## 2. Service Layer

- [ ] 2.1 Create `services/prompt_service.py` with PromptService class
- [ ] 2.2 Implement `create_prompt(name, template, variables)` — validate template, create PromptModel + PromptVersionModel(v1)
- [ ] 2.3 Implement `get_prompt(prompt_id)` — return prompt with current version
- [ ] 2.4 Implement `update_prompt(prompt_id, template?, labels?)` — update and create new version
- [ ] 2.5 Implement `delete_prompt(prompt_id)` — soft delete
- [ ] 2.6 Implement `list_prompts(workspace_id, page, page_size)` — paginated list
- [ ] 2.7 Implement `list_versions(prompt_id)` — all versions ordered by version number
- [ ] 2.8 Implement `rollback_to_version(prompt_id, target_version)` — create new version with target's template
- [ ] 2.9 Implement `get_by_label(label)` — get prompt by deployment label

## 3. Template Engine

- [ ] 3.1 Create `services/template_engine.py` with TemplateEngine class
- [ ] 3.2 Implement `render(template, variables)` — Jinja2 SandboxedEnvironment rendering
- [ ] 3.3 Implement `validate(template)` — validate Jinja2 syntax
- [ ] 3.4 Implement `extract_variables(template)` — extract variable names from template

## 4. API Layer

- [ ] 4.1 Create `api/management/prompts.py` with CRUD endpoints
- [ ] 4.2 Implement POST/GET/PUT/DELETE /api/prompts
- [ ] 4.3 Implement GET /api/prompts/{id}/versions and POST rollback
- [ ] 4.4 Implement GET /api/prompts/by-label/{label}
- [ ] 4.5 Register prompt router in main FastAPI app

## 5. Testing

- [ ] 5.1 Unit tests for PromptService — CRUD, versions, rollback
- [ ] 5.2 Unit tests for TemplateEngine — render, validate, extract
- [ ] 5.3 Integration tests for API endpoints
