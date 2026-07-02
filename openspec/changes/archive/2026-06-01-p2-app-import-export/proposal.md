## Why

Users configure agents with specific personas, tools, skills, knowledge bases, and workflows. Currently, there's no way to export an agent's complete configuration for backup, migration, or sharing. The feature catalog describes this as "Agent 应用整体导入导出，支持备份、迁移、跨环境复制" with reference to AgentArts.

This change adds import/export functionality that allows users to download an agent's complete configuration as a JSON file and import it into another environment.

## What Changes

- Add **Export endpoint** — `GET /api/agents/{id}/export` returns a JSON file with agent config, workflow, and memory blocks
- Add **Import endpoint** — `POST /api/agents/import` creates a new agent from exported JSON
- Add **Frontend export button** — "Export" button on agent detail page
- Add **Frontend import page** — "Import Agent" button on agents list page with file upload
- Add **Export format** — JSON schema for portable agent configuration

## Capabilities

### New Capabilities
- `app-import-export`: Agent configuration export/import for backup, migration, and sharing

### Modified Capabilities
- (none — new feature)

## Impact

- **Backend**: New endpoints in `src/hecate/api/management/agents.py`
- **Frontend**: Export button on agent detail, import button on agents list
- **Tests**: Export/import endpoint tests
- **Format**: JSON with agent config, workflow graph DSL, memory blocks
