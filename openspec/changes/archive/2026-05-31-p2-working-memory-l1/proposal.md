## Why

L1 working memory is **fully implemented in the backend** — MemoryBlockModel, WorkingMemoryService, API endpoints, and tests all exist. However, the feature is invisible to users because there is **no frontend UI** to manage memory blocks, no integration with the agent configurator, and no templates for common use cases. Users must use the API directly to create and manage memory blocks, which defeats the purpose of a low-code platform.

This change closes the frontend gap to make L1 working memory a user-facing feature.

## What Changes

- Add a **Memory tab** to the agent configurator showing the agent's memory blocks with inline editing
- Add a **Memory Block Manager** component for creating, editing, and deleting memory blocks
- Add **memory block templates** (persona, user_profile, domain_context, task_tracker) that users can add with one click
- Show active memory blocks in the **agent detail page** with quick edit capability
- Add memory block indicators in the **chat page** showing which blocks are active

## Capabilities

### New Capabilities
- `memory-block-management`: Frontend UI for L1 working memory block CRUD — agent configurator integration, inline editing, templates, and chat page indicators

### Modified Capabilities
- `agent-configurator`: Add Memory tab for managing agent's memory blocks with template support
- `session-memory`: Add requirement for frontend display of active memory blocks in chat

## Impact

- **Frontend only** — no backend changes needed (all APIs exist)
- `web/src/components/agent/` — new memory-block-editor component
- `web/src/components/agent/agent-configurator.tsx` — add Memory tab
- `web/src/app/(dashboard)/agents/[id]/page.tsx` — show memory blocks section
- `web/src/app/(dashboard)/chat/[conversationId]/page.tsx` — show active memory block indicators
- **Tests**: Vitest component tests for memory block editor
