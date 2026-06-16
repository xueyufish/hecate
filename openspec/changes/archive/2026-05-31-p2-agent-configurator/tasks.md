## 1. Shared Components

- [x] 1.1 Create `web/src/components/agent/agent-configurator.tsx` — main tabbed configurator component with Basic, Knowledge, Tools, Advanced tabs
- [x] 1.2 Create `web/src/components/agent/knowledge-selector.tsx` — multi-select component for knowledge bases
- [x] 1.3 Create `web/src/components/agent/skill-selector.tsx` — multi-select component for skills
- [x] 1.4 Create `web/src/components/agent/tool-selector.tsx` — multi-select component for tools
- [x] 1.5 Create `web/src/components/agent/model-selector.tsx` — grouped dropdown with provider sections and availability indicators

## 2. API Integration

- [x] 2.1 Create `web/src/lib/api-types.ts` — TypeScript interfaces for Agent, Tool, Skill, KnowledgeBase API responses
- [x] 2.2 Add API client methods for `GET /api/tools`, `GET /api/skills`, `GET /api/knowledge-bases` in `web/src/lib/api-client.ts`

## 3. Create Agent Page

- [x] 3.1 Rewrite `web/src/app/(dashboard)/agents/new/page.tsx` to use AgentConfigurator in create mode
- [x] 3.2 Implement form submission — POST to `/api/agents` with all configured fields
- [x] 3.3 Implement success navigation — redirect to `/agents/{new_id}` on success
- [x] 3.4 Implement error handling — display API errors, keep form data intact

## 4. Edit Agent Page

- [x] 4.1 Rewrite `web/src/app/(dashboard)/agents/[id]/page.tsx` to use AgentConfigurator in edit mode
- [x] 4.2 Implement data loading — fetch agent data and populate form fields
- [x] 4.3 Implement form submission — PUT to `/api/agents/{id}` with modified fields
- [x] 4.4 Implement success handling — show success toast, stay on page

## 5. Verification

- [x] 5.1 Run `cd web && npm run lint` — zero errors (1 pre-existing warning)
- [x] 5.2 Run `cd web && npm run build` — successful build (fixed pre-existing dsl-bridge.ts type error)
- [ ] 5.3 Manual test — create agent with all fields, verify API stores correctly
- [ ] 5.4 Manual test — edit agent, verify fields load and update correctly
