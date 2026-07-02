## 1. Backend: Agent Template Files

- [x] 1.1 Create `src/hecate/data/agent_templates/` directory
- [x] 1.2 Create `customer-service.json` template
- [x] 1.3 Create `code-review.json` template
- [x] 1.4 Create `research-assistant.json` template
- [x] 1.5 Create `content-writer.json` template
- [x] 1.6 Create `data-analyst.json` template

## 2. Backend: Template API

- [x] 2.1 Create `src/hecate/api/management/agent_templates.py` with template loading and caching
- [x] 2.2 Implement `GET /api/agent-templates` endpoint (list with metadata)
- [x] 2.3 Implement `GET /api/agent-templates/{id}` endpoint (full template)
- [x] 2.4 Implement `POST /api/agent-templates/{id}/instantiate` endpoint with KB ID validation
- [x] 2.5 Register router in main app

## 3. Backend: Tests

- [x] 3.1 Add tests for template list endpoint
- [x] 3.2 Add tests for template detail endpoint (success + 404)
- [x] 3.3 Add tests for template instantiation (success + invalid KB IDs)

## 4. Frontend: Template Picker

- [x] 4.1 Create `web/src/components/agent/template-picker.tsx` component
- [x] 4.2 Display templates grouped by category with preview cards
- [x] 4.3 Add "From Template" button to agent creation page
- [x] 4.4 Pre-fill form when template is selected

## 5. Verification

- [x] 5.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 5.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 5.3 Run `mypy src/` — zero errors
- [x] 5.4 Run `python -m pytest tests/ -q` — all tests pass
- [x] 5.5 Run `npm run lint` and `npm run build` in `web/` — zero errors
