## 1. Frontend: Memory Block Editor Component

- [x] 1.1 Create `web/src/components/agent/memory-block-editor.tsx` component that displays a list of memory blocks with inline editing, save/cancel buttons, and delete confirmation
- [x] 1.2 Add block content preview (first 100 chars) with expand/collapse for full content
- [x] 1.3 Add loading state while fetching blocks and error state display
- [x] 1.4 Implement inline edit mode: click content → textarea + Save/Cancel buttons

## 2. Frontend: Memory Block Templates

- [x] 2.1 Define template data in `memory-block-editor.tsx` with 4 templates: persona, user_profile, domain_context, task_tracker
- [x] 2.2 Add template dropdown/button that creates a block from template with one click
- [x] 2.3 Handle 409 conflict error when template label already exists (show user-friendly message)

## 3. Frontend: Create Custom Block Form

- [x] 3.1 Add "Add Block" button that opens a form with fields: label (required), content, position (default 0), limit (default 2000)
- [x] 3.2 Form validation: label required, max length 100; content max length 50000; limit > 0
- [x] 3.3 Submit calls `POST /api/agents/{id}/memory-blocks`, handles 409 for duplicate labels

## 4. Frontend: Agent Configurator Integration

- [x] 4.1 Add "Memory" tab to `AgentConfigurator` component (5th tab after Tools)
- [x] 4.2 Memory tab shows `MemoryBlockEditor` component when in edit mode (agent_id available)
- [x] 4.3 In create mode, Memory tab shows message "Save agent first, then add memory blocks"

## 5. Frontend: Agent Detail Page

- [x] 5.1 Add "Memory Blocks" section to agent detail page showing block labels and content previews
- [x] 5.2 Add "Edit in Configurator" link that navigates to `/agents/[id]` with Memory tab active
- [x] 5.3 Show "No memory blocks configured" with link to add when agent has no blocks

## 6. Frontend: Chat Page Memory Indicators

- [x] 6.1 Fetch agent's memory blocks in chat page useEffect (alongside knowledge_base_ids)
- [x] 6.2 Display memory block labels as badges in chat header (similar to KB badges)
- [x] 6.3 Show nothing when agent has no memory blocks

## 7. Verification

- [x] 7.1 Run `npm run lint` in `web/` — zero errors (1 pre-existing warning)
- [x] 7.2 Run `npm run build` in `web/` — zero errors
