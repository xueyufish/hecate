## Why

The Hecate frontend codebase contains ~236 hardcoded Chinese strings across 26 files. As the project prepares to go public on GitHub after P2, all code must be in English to maintain a professional, consistent codebase alongside the recently translated documentation.

## What Changes

- Translate all Chinese UI text in `web/src/` to English (labels, placeholders, alerts, status messages, node type names)
- Fix the "handoff" edge label inconsistency: change `"移交"` to `"Handoff"` across `dsl-bridge.ts` and `canvas-area.tsx`, and update all equality checks
- Fix typo: `"knowledge-rerieval"` → `"knowledge-retrieval"` in `config-panel.tsx`
- Update `dsl-bridge.test.ts` assertions to match translated strings
- Update `node-palette.tsx` Chinese type labels to English
- Translate all `alert()` messages (validation success/failure, errors)

## Capabilities

### New Capabilities

_(none — this is a translation/refactoring change, no new capabilities)_

### Modified Capabilities

_(none — no spec-level behavior changes, only UI text translation)_

## Impact

- **Frontend**: All 26 files in `web/src/` with Chinese strings (components, pages, lib, tests)
- **Tests**: `dsl-bridge.test.ts` has Chinese assertions that must be updated
- **No backend/API changes**: Only frontend string literals affected
- **No breaking changes**: Functionality remains identical, only display text changes
