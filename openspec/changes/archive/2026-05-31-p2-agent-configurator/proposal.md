## Why

The current Agent creation page (`/agents/new`) only supports basic fields: name, description, model, and system prompt. Users cannot configure tools, skills, knowledge bases, memory settings, or advanced options like opening remarks and risk level. This forces users to manually edit the API or database to fully configure an Agent, creating friction and limiting adoption.

The Agent Configurator provides a comprehensive visual form that exposes all AgentModel fields, making it easy to configure an Agent's complete behavior through the UI.

## What Changes

- **Enhanced Agent creation form**: Replace the basic form with a tabbed configurator covering all AgentModel fields
- **Enhanced Agent edit form**: Apply the same configurator to the `/agents/[id]` edit page
- **Tool selector**: Multi-select component to browse and select available tools
- **Knowledge base selector**: Multi-select component to browse and select knowledge bases
- **Skill selector**: Multi-select component to browse and select skills
- **Model selector enhancement**: Grouped dropdown with provider sections and availability indicators
- **Memory configuration**: Toggle for L1/L2/L3 memory features
- **Advanced settings**: Opening remarks, enable suggestions, risk level

## Capabilities

### New Capabilities
- `agent-configurator`: Visual form-based Agent configuration UI with tabbed sections for persona, model, tools, knowledge bases, skills, memory, and advanced settings

### Modified Capabilities
- (none — this is a frontend-only change; backend API unchanged)

## Impact

- **Frontend**: `web/src/app/(dashboard)/agents/new/page.tsx` — complete rewrite
- **Frontend**: `web/src/app/(dashboard)/agents/[id]/page.tsx` — complete rewrite
- **Frontend**: New components in `web/src/components/agent/` — configurator tabs, selectors
- **API**: No backend changes required — existing CRUD endpoints support all fields
- **Dependencies**: No new dependencies — uses existing shadcn/ui components
