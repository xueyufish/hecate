## Context

Hecate's frontend is a Next.js app in `web/` using shadcn/ui components. The existing Agent pages (`/agents/new`, `/agents/[id]`) have basic forms with only name, description, model, and system prompt fields. The `AgentModel` in the backend supports many more fields: tools, skills, knowledge_base_ids, risk_level, opening_remarks, enable_suggestions, mode, workflow_id.

The Agent Configurator enhances these pages with a tabbed form that exposes all configuration options. The backend API already supports all fields — no backend changes are needed.

## Goals / Non-Goals

**Goals:**
- Provide a complete visual form for configuring all AgentModel fields
- Use tabbed sections to organize configuration into logical groups
- Support both create and edit flows with the same component
- Reuse existing shadcn/ui components (no new dependencies)
- Load available tools, skills, and knowledge bases from API for selection

**Non-Goals:**
- Workflow mode configuration (requires workflow canvas integration — separate feature)
- Agent testing/preview (separate feature)
- Bulk agent operations (P3 concern)
- Agent templates/presets (1.1.5 scenario packaging — separate feature)

## Decisions

### D1: Tabbed form layout

**Decision**: Organize the configurator into 4 tabs: Basic, Knowledge, Tools, Advanced.

**Rationale**: The AgentModel fields naturally group into these categories. Tabs keep the form manageable without overwhelming users.

**Tab structure:**
- **Basic**: Name, persona (system prompt), model selector, mode
- **Knowledge**: Knowledge base multi-select, skill multi-select
- **Tools**: Tool multi-select with categories
- **Advanced**: Risk level, opening remarks, enable suggestions, memory toggles

**Alternatives considered:**
- *Single long form*: Too overwhelming for users.
- *Wizard/stepper*: Over-engineered for a configuration form.
- *Accordion*: Less discoverable than tabs.

### D2: Reuse existing selector pattern

**Decision**: Create reusable multi-select components for tools, skills, and knowledge bases using the existing shadcn/ui Select + Checkbox pattern.

**Rationale**: Consistent with existing UI patterns. No new dependencies. Simple to implement.

**Alternatives considered:**
- *Third-party multi-select library*: Adds dependency, inconsistent styling.
- *Custom dropdown with search*: Over-engineered for P2 scope.

### D3: Shared configurator component

**Decision**: Create a single `AgentConfigurator` component used by both `/agents/new` and `/agents/[id]` pages.

**Rationale**: DRY — same form logic for create and edit. The component accepts initial data (empty for new, populated for edit) and an `onSubmit` callback.

**Alternatives considered:**
- *Separate forms for create/edit*: Code duplication, maintenance burden.
- *Form library (react-hook-form)*: Over-engineered for this scope.

## Risks / Trade-offs

- **[API calls for selectors]** Loading tools, skills, KBs requires 3 API calls on page load → Mitigation: Parallel fetch, loading states.
- **[Form complexity]** Many fields may confuse users → Mitigation: Tabs organize fields logically; sensible defaults.
- **[Edit mode data loading]** Edit page needs to fetch agent + available options → Mitigation: Parallel fetch, skeleton loading.
