## Why

Users currently configure agents from scratch each time — selecting persona, model, tools, skills, and knowledge bases. Many use cases share common patterns: a customer service agent, a code review agent, a research assistant. The feature catalog describes this as "把配置好的 Agent 打包成可复用的场景方案" with reference to AgentArts.

This change adds agent templates — pre-configured agent definitions that users can instantiate with one click, then customize as needed.

## What Changes

- Add **AgentTemplate model** — JSON schema defining reusable agent configurations (persona, model, tools, skills, KBs, memory blocks)
- Add **Template API** — `GET /api/agent-templates` to list, `GET /api/agent-templates/{id}` to get details
- Add **Template instantiation** — `POST /api/agent-templates/{id}/instantiate` creates a new agent from template
- Add **Built-in templates** — 5 pre-configured templates (customer service, code review, research assistant, content writer, data analyst)
- Add **Frontend template picker** — "From Template" button on agent creation page

## Capabilities

### New Capabilities
- `agent-templates`: Agent template system with built-in templates, API, and frontend picker

### Modified Capabilities
- (none — new feature)

## Impact

- **Backend**: New `src/hecate/data/agent_templates/` directory with JSON files
- **Backend**: New `src/hecate/api/management/agent_templates.py` API
- **Frontend**: Template picker component in agent creation page
- **Tests**: API endpoint tests
- **Pattern**: Follows existing orchestration template pattern (file-based JSON, caching)
