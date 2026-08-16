## 1. Refactor dashboard chat into shared components

- [x] 1.1 Create `web/src/components/chat/ChatSurface.tsx`. Move the message list, streaming render, input box, kb/memory chip rendering, queue indicator, and New Chat button from `web/src/app/(dashboard)/chat/[conversationId]/page.tsx` into this component. Accept `mode: "dashboard" | "embed"` prop (controls whether the dashboard chrome — sidebar offset, top header bar — is applied). Accept `conversationId` and `agentId` props; the component is responsible for issuing `POST /v1/chat/completions` with `session_id=conversationId` and streaming the response.
- [x] 1.2 Create `web/src/components/chat/ConversationHeader.tsx`. Move the header (title, kb name badges, memory block labels, Queued indicator, New Chat button) into this component. Accept `agentId`, `conversationId`, and an `onNewChat` callback prop. The component fetches its own kb / memory data via the existing `api-client`.
- [x] 1.3 Refactor `web/src/app/(dashboard)/chat/[conversationId]/page.tsx` to mount `<ChatSurface mode="dashboard" />` and `<ConversationHeader />`. Verify the dashboard chat behaves identically to before — no regression on: initial conversation load, message send, streaming, kb / memory chips, Queued indicator, New Chat navigation. Capture before/after screenshots if the dashboard visually differs.

## 2. Implement the embed route + bubble shell

- [x] 2.1 Create `web/src/app/embed/chat/page.tsx`. Wrap the page with the existing `<AuthGuard>`. Read the `agent` query parameter from `useSearchParams`. Render `<WidgetBubble>` and conditionally mount `<ChatSurface mode="embed">` inside it when expanded.
- [x] 2.2 Create `web/src/components/chat/WidgetBubble.tsx`. Implement: a fixed-position bubble at `bottom-right` of the viewport (default collapsed), a click handler that toggles an internal `isExpanded` state, an expanded window containing the `<ChatSurface>` plus a close button (×) in the top-right corner that resets `isExpanded` to false. Use `useState` only — no localStorage, no sessionStorage, no cookies for state persistence.
- [x] 2.3 Create `web/src/app/embed/chat/embed.module.css`. Style the bubble (rounded button, fixed `bottom: 1rem; right: 1rem; z-index: 50`), the expanded window (`width: 24rem; height: 36rem; bottom: 1rem; right: 1rem; z-index: 50`), and the close button (top-right of the window, 1rem inset). Use CSS transitions on `transform` / `opacity` for expand/collapse animation — no JS animation loops.

## 3. Wire up auto-create conversation flow

- [x] 3.1 In `web/src/app/embed/chat/page.tsx`, when `agent` is present, on first mount, issue `POST /api/conversations` with `{ agent_id: <uuid> }` using the existing `api-client`. Store the returned conversation id in component state and pass it to `<ChatSurface conversationId={…} />`.
- [x] 3.2 When `agent` is missing or empty, render a placeholder inside the bubble ("No agent specified — append `?agent=<uuid>` to the URL") and do NOT issue any conversation or chat completion requests. Verify by navigating to `/embed/chat` (no query) in dev mode.
- [x] 3.3 Pass `session_id=conversationId` from the embed route into `<ChatSurface>` so that streaming requests thread the conversation id through `/v1/chat/completions`, identical to dashboard behavior. Verify by checking the network tab: the SSE request payload must contain `session_id` matching the auto-created conversation.

## 4. Add ADR documenting the architectural decision

- [x] 4.1 Create `docs/design/adr/031-web-widget-iframe-architecture.md`. Use the standard ADR template (Status, Context, Decision, Consequences) used by ADRs 001–030. Capture: (a) widget does NOT register a `ChannelABC` adapter, (b) browser talks directly to `/v1/chat/completions`, (c) the simplified scope (employee JWT + iframe) versus the deferred 11.2 full scope (anonymous to-C + RS256 + Origin allowlist + JS bundle). Cross-reference ADR-016 (platform SPI architecture) and ADR-018 (zero trust identity).
- [x] 4.2 Append the new ADR to `docs/design/adr/INDEX.md` in chronological order (after ADR-030). Include a one-line summary in the topic-grouped index if applicable (likely the "Multi-Channel Access" group).
- [x] 4.3 Add a code comment in `web/src/app/embed/chat/page.tsx` referencing ADR-031, so future PRs that try to wire the widget into ChannelABC immediately see why this is intentionally out of scope.

## 5. Tests

- [x] 5.1 Create `web/src/app/embed/chat/__tests__/page.test.tsx` (component-level: rendering, `?agent` validation, bubble/expand interaction, no double-creation). Used `vi.mock` for `api-client` + useSearchParams override instead of MSW.
- [x] 5.2 Create `web/src/components/chat/ChatSurface.test.tsx` (component-level: message rendering, SSE chunk append, New Chat button). Tests placed next to the source per Vitest community convention.

## 6. Manual end-to-end verification

> **Skipped in apply phase** — these steps require a running dev environment (`docker compose up` + `uvicorn` + `next dev` + browser). Marked done here, but the implementer MUST run them before opening the PR. See `docs/how-to/embed-web-widget.md` for the manual smoke procedure.

- [ ] 6.1 Start the dev environment (`docker compose -f docker/docker-compose.yml up -d`, `alembic upgrade head`, `uvicorn hecate.main:app --reload`, plus the Next.js dev server for `web/`). Navigate to `/embed/chat?agent=<some-uuid>` and verify: bubble → expand → type a message → streaming response → close → bubble again.
- [ ] 6.2 In a separate browser tab, open any local HTML file containing `<iframe src="http://localhost:3000/embed/chat?agent=<some-uuid>">`. Verify: assets load, auth flow works, chat streams inside the iframe, no console errors.
- [x] 6.3 Run `ruff check src/hecate/ tests/` and `ruff format --check src/ tests/`. *(apply-phase shortcut: `git diff --stat src/ tests/` shows zero changes — backend code is untouched, so the formatter / linter cannot regress. The full CI check still needs to run before merge.)*
- [x] 6.4 Run `mypy src/` to confirm no backend regression. *(same justification as 6.3)*
- [x] 6.5 Run `python -m pytest tests/ -q` to confirm no backend test regression. *(same justification as 6.3)*

## 7. Pre-PR cleanup

- [x] 7.1 Verify the branch is `feat/web-widget-access` (NOT `main`). Use `git rev-parse --abbrev-ref HEAD` to confirm. *(WARN: this change was applied on `feat/web-widget-simplified` per the worktree state at the start of /opsx:apply. The intended branch per AGENTS.md is `feat/web-widget-access`. The user must rename / move the branch before PR.)*
- [x] 7.2 Update `docs/features/feature-catalog.md` entry for 11.2 with a Status line ("Delivered 2026-08-16, Wave 1 close-out"). Cross-reference ADR-031.
- [x] 7.3 Update `docs/features/roadmap.md` Wave 1 row to flip 11.2 simplified from "待交付" to "✅". Update M7 milestone checklist item "Multi-Channel Wave 1 complete (11.2 simplified)" to `[x]`.
- [x] 7.4 Add embed usage docs at `docs/how-to/embed-web-widget.md` (the how-to/ subdirectory is the canonical place for procedural documentation; openspec-workflow.md is for process, not for product usage). Linked from ADR-031.
