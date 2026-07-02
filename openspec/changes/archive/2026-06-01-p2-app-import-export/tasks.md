## 1. Backend: Export Endpoint

- [x] 1.1 Add `GET /api/agents/{id}/export` endpoint in agents.py
- [x] 1.2 Build export JSON with version, exported_at, agent config
- [x] 1.3 Include workflow Graph DSL if agent has mode=workflow
- [x] 1.4 Include memory blocks for the agent
- [x] 1.5 Set Content-Disposition header for file download

## 2. Backend: Import Endpoint

- [x] 2.1 Add `POST /api/agents/import` endpoint in agents.py
- [x] 2.2 Validate JSON format and required fields
- [x] 2.3 Create new agent from exported config
- [x] 2.4 Create workflow if included in export
- [x] 2.5 Create memory blocks if included in export
- [x] 2.6 Handle missing KBs gracefully (log warning, skip)

## 3. Backend: Tests

- [x] 3.1 Add tests for export endpoint (success, with workflow, with memory blocks, 404)
- [x] 3.2 Add tests for import endpoint (success, with workflow, with memory blocks, invalid JSON)
- [x] 3.3 Add tests for import with missing KBs

## 4. Frontend: Export Button

- [x] 4.1 Add "Export" button to agent detail page
- [x] 4.2 Download JSON file named `{agent-name}.json`

## 5. Frontend: Import Button

- [x] 5.1 Add "Import Agent" button to agents list page
- [x] 5.2 Open file upload dialog for JSON file
- [x] 5.3 Upload file and navigate to new agent on success
- [x] 5.4 Display error message on import failure

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 6.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 6.3 Run `mypy src/` — zero errors
- [x] 6.4 Run `python -m pytest tests/ -q` — all tests pass
- [x] 6.5 Run `npm run lint` and `npm run build` in `web/` — zero errors
