## Context

L1 working memory is fully implemented in the backend:
- **Model**: `MemoryBlockModel` with agent_id, label, content, position, limit
- **Service**: `WorkingMemoryService` with CRUD + `inject_blocks()` + `update_memory_block` tool
- **API**: `POST/GET/PUT/DELETE /api/agents/{id}/memory-blocks`
- **Integration**: `ConversationService` loads blocks before each turn, passes to `ContextAssembler`
- **Tests**: Full API test coverage in `tests/test_api/test_memory.py`

However, there is **zero frontend UI** for this feature. Users cannot see, create, edit, or delete memory blocks without using the API directly. The agent configurator has no memory management section. The chat page does not show which memory blocks are active.

## Goals / Non-Goals

**Goals:**
- Add a Memory tab to the agent configurator for managing memory blocks
- Provide inline editing for memory block content
- Offer one-click templates for common block types (persona, user_profile, domain_context, task_tracker)
- Show active memory blocks on the agent detail page
- Display memory block indicators in the chat header

**Non-Goals:**
- Backend changes (all APIs exist)
- Memory block versioning or history
- Memory block sharing between agents
- Memory analytics or access tracking UI
- Real-time collaborative editing of memory blocks

## Decisions

### D1: Memory tab in existing AgentConfigurator (not separate page)

**Decision**: Add a "Memory" tab to the existing `AgentConfigurator` component alongside Basic, Knowledge, Tools, Advanced.

**Rationale**: Consistent with existing UX pattern. Users configure agent's memory blocks during agent setup. No new page navigation needed.

**Alternatives considered**:
- Separate `/agents/[id]/memory` page — adds navigation complexity, splits agent config across pages
- Modal dialog — limited space for editing multiple blocks

### D2: Inline editing with save/cancel (not auto-save)

**Decision**: Memory block content is edited inline with explicit Save/Cancel buttons per block.

**Rationale**: Memory blocks are important configuration. Auto-save could cause accidental overwrites. Explicit save gives users control.

**Alternatives considered**:
- Auto-save on blur — risky for important config, no undo
- Edit mode toggle — adds UX complexity

### D3: Template system with predefined block types

**Decision**: Offer 4 templates: persona, user_profile, domain_context, task_tracker. Each has a predefined label, suggested content, position, and limit.

**Rationale**: Reduces friction for common use cases. Users can still create custom blocks.

**Templates**:
| Template | Label | Content Hint | Position | Limit |
|----------|-------|--------------|----------|-------|
| Persona | `persona` | "You are a helpful assistant that..." | 0 | 2000 |
| User Profile | `user_profile` | "The user prefers..." | 1 | 1000 |
| Domain Context | `domain_context` | "This agent operates in the domain of..." | 2 | 2000 |
| Task Tracker | `task_tracker` | "Current task: ... Progress: ..." | 3 | 1500 |

### D4: Chat page shows block labels only (not content)

**Decision**: The chat page header shows memory block labels as badges, similar to KB indicators. Content is not displayed to avoid clutter.

**Rationale**: Keeps chat UI clean. Users can see which blocks are active without content noise.

## Risks / Trade-offs

- **[No backend validation for template fields]** → Templates are frontend-only convenience. Backend already validates label uniqueness and field constraints.
- **[Large memory blocks could bloat context]** → The `limit` field on each block already enforces token limits. Frontend should display the limit prominently.
- **[Race condition on concurrent edits]** → Multiple users editing the same agent's blocks could conflict. Mitigation: last-write-wins (acceptable for single-user mode).
