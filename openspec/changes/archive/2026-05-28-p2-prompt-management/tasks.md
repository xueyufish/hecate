## 1. Data Models

- [x] 1.1 Create `PromptModel` ORM in `models/prompt.py` — fields: id, workspace_id, name, created_at, updated_at, deleted_at
- [x] 1.2 Create `PromptVersionModel` ORM in `models/prompt.py` — fields: id, prompt_id, version, template(TEXT), variables(JSONB), labels(JSONB), created_at
- [x] 1.3 Create Pydantic schemas: PromptCreateSchema, PromptUpdateSchema, PromptReadSchema, PromptVersionReadSchema
- [x] 1.4 Generate Alembic migration for prompts and prompt_versions tables
- [x] 1.5 Update `alembic/env.py` to import prompt model

## 2. Service Layer

- [x] 2.1 Create `services/prompt_service.py` with PromptService class
- [x] 2.2 Implement `create_prompt(name, template, variables)` — validate template, create PromptModel + PromptVersionModel(v1)
- [x] 2.3 Implement `get_prompt(prompt_id)` — return prompt with current version
- [x] 2.4 Implement `update_prompt(prompt_id, template?, labels?)` — update and create new version
- [x] 2.5 Implement `delete_prompt(prompt_id)` — soft delete
- [x] 2.6 Implement `list_prompts(workspace_id, page, page_size)` — paginated list
- [x] 2.7 Implement `list_versions(prompt_id)` — all versions ordered by version number
- [x] 2.8 Implement `rollback_to_version(prompt_id, target_version)` — create new version with target's template
- [x] 2.9 Implement `get_by_label(label)` — get prompt by deployment label

## 3. Template Engine

- [x] 3.1 Create `services/template_engine.py` with TemplateEngine class
- [x] 3.2 Implement `render(template, variables)` — Jinja2 SandboxedEnvironment rendering
- [x] 3.3 Implement `validate(template)` — validate Jinja2 syntax
- [x] 3.4 Implement `extract_variables(template)` — extract variable names from template

## 4. API Layer

- [x] 4.1 Create `api/management/prompts.py` with CRUD endpoints
- [x] 4.2 Implement POST/GET/PUT/DELETE /api/prompts
- [x] 4.3 Implement GET /api/prompts/{id}/versions and POST rollback
- [x] 4.4 Implement GET /api/prompts/by-label/{label}
- [x] 4.5 Register prompt router in main FastAPI app

## 5. Testing

- [x] 5.1 Unit tests for PromptService — CRUD, versions, rollback
- [x] 5.2 Unit tests for TemplateEngine — render, validate, extract
- [x] 5.3 Integration tests for API endpoints
