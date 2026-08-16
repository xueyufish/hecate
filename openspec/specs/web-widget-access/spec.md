## Purpose

Lets operators embed Hecate's chat capability into Hecate's own portal and into customer intranet portals as a floating bubble + expandable chat window, reusing the existing employee JWT auth and the existing `/v1/chat/completions` streaming endpoint with no backend changes.

## Requirements

### Requirement: Embed route renders a floating bubble expandable into a chat window

The system SHALL expose a route `/embed/chat` that, when visited in a browser, renders a small floating bubble anchored to the bottom-right corner of the viewport. Clicking the bubble SHALL expand it into a chat window containing the same conversation UI as the existing dashboard chat. The chat window SHALL include a close button (×) in its top-right corner; clicking close SHALL collapse the window back to the bubble state without navigating away from `/embed/chat`. State (collapsed vs expanded) SHALL NOT be persisted across page reloads — reloading the page SHALL always start with the bubble collapsed.

#### Scenario: Initial render shows collapsed bubble

- **WHEN** an authenticated user navigates to `/embed/chat?agent=<uuid>`
- **THEN** the route SHALL render a single bubble element at the bottom-right of the viewport and SHALL NOT render the chat window

#### Scenario: Click expands to chat window

- **WHEN** the user clicks the bubble
- **THEN** the route SHALL render the chat window (containing a message list, an input box, and the conversation header) anchored above the bubble and SHALL hide the bubble

#### Scenario: Close button collapses back to bubble

- **WHEN** the chat window is expanded and the user clicks the close button (×)
- **THEN** the chat window SHALL be removed from the DOM and the bubble SHALL reappear at the bottom-right
- **THEN** the route URL SHALL remain `/embed/chat?agent=<uuid>` (no navigation)

#### Scenario: Reload restores collapsed bubble

- **WHEN** the user has the chat window expanded and then reloads the page
- **THEN** the route SHALL re-render with the bubble collapsed (no chat window visible)

### Requirement: Embed route requires an `?agent=` query parameter

The system SHALL read the `agent` query parameter from the URL on load. If `agent` is missing or empty, the route SHALL render a "no agent specified" placeholder inside the bubble (or expanded window) and SHALL NOT issue any conversation or chat completion requests. If `agent` is present, the route SHALL use it as the target agent for the auto-created conversation described in the next requirement.

#### Scenario: Missing agent shows placeholder

- **WHEN** an authenticated user navigates to `/embed/chat` with no query parameters
- **THEN** the route SHALL render a placeholder message indicating that `agent` is required
- **THEN** the route SHALL NOT call `POST /api/conversations` and SHALL NOT call `POST /v1/chat/completions`

#### Scenario: Present agent triggers conversation creation

- **WHEN** an authenticated user navigates to `/embed/chat?agent=<uuid>`
- **THEN** the route SHALL proceed to auto-create a conversation (see auto-create requirement)

### Requirement: Embed route auto-creates a conversation on first load

On first successful load with a valid `agent` query parameter, the route SHALL call `POST /api/conversations` with `{ agent_id: <uuid> }` to create a new conversation, then SHALL use the returned `id` as `session_id` for all subsequent `POST /v1/chat/completions` calls issued from the embed window. The auto-created conversation SHALL persist in the user's conversation history like any other conversation created from the dashboard. Reloading `/embed/chat?agent=<uuid>` SHALL create a new conversation each time.

#### Scenario: First load creates conversation

- **WHEN** `/embed/chat?agent=<uuid>` is loaded by an authenticated user for the first time
- **THEN** the route SHALL issue `POST /api/conversations` with `{ agent_id: <uuid> }`
- **THEN** upon receiving the created conversation's `id`, the route SHALL store it as the active `sessionId` for the chat window

#### Scenario: User message streams through `/v1/chat/completions`

- **WHEN** the user submits a message in the chat window
- **THEN** the chat window SHALL issue `POST /v1/chat/completions` with `session_id` set to the auto-created conversation id and stream the response back into the assistant message bubble

#### Scenario: Reload creates a fresh conversation

- **WHEN** the user reloads `/embed/chat?agent=<uuid>` after using the widget
- **THEN** the route SHALL issue a new `POST /api/conversations` call (resulting in a new conversation id), independent of the previous session

### Requirement: Chat window reuses dashboard chat UI behavior

The chat window inside the embed route SHALL render the same conversation surface as the existing dashboard chat, including: a message list with user / assistant / tool roles, a streaming-text input box, an in-flight indicator while the assistant is streaming, a "Queued..." indicator when the request is queued, knowledge-base and memory-block labels in the header, and a "New Chat" button that creates an additional conversation. The "New Chat" button SHALL behave identically to the dashboard version — it SHALL issue `POST /api/conversations` and reset the message list to empty.

#### Scenario: User and assistant messages render

- **WHEN** the chat window contains messages
- **THEN** user messages SHALL be rendered right-aligned and assistant messages SHALL be rendered left-aligned with the same color scheme used by the dashboard chat

#### Scenario: Streaming chunk appends to assistant message

- **WHEN** the chat window receives a streaming chunk from `/v1/chat/completions`
- **THEN** the chunk text SHALL be appended to the in-progress assistant message and the message SHALL be re-rendered

#### Scenario: New Chat button creates a fresh conversation

- **WHEN** the user clicks the "New Chat" button
- **THEN** the chat window SHALL issue `POST /api/conversations` with the same `agent_id`, clear the message list, and SHALL display the new conversation id internally (the URL SHALL remain `/embed/chat?agent=<uuid>`)

### Requirement: Auth uses the existing employee JWT pipeline

The embed route SHALL require an authenticated Hecate session. The route SHALL be wrapped by the existing `<AuthGuard>` component, identical to the dashboard routes. If the user is not authenticated, the route SHALL redirect to `/login` (within the iframe, accepting the navigation). If the user's access token has expired and the next `POST /v1/chat/completions` or `POST /api/conversations` returns `401`, the existing `api-client` 401 interceptor SHALL clear the local tokens and navigate to `/login` — this behavior SHALL be inherited from the dashboard chat without modification.

#### Scenario: Unauthenticated user redirected to login

- **WHEN** an unauthenticated user navigates to `/embed/chat?agent=<uuid>`
- **THEN** the `<AuthGuard>` SHALL redirect to `/login` (within the iframe)

#### Scenario: Expired token triggers re-login

- **WHEN** an authenticated user submits a message and the access token has expired (server returns `401`)
- **THEN** the `api-client` 401 interceptor SHALL clear `access_token` and `refresh_token` from localStorage and navigate to `/login`

### Requirement: Embed route is isolated from the host page via iframe-friendly rendering

The embed route SHALL be designed to load inside an `<iframe>` on a host page (Hecate portal or customer intranet). All required assets (CSS, fonts, JS) SHALL be served from the same Hecate origin as the route, with relative or origin-absolute URLs that resolve correctly when the document is rendered inside an iframe. The route SHALL NOT rely on `window.top`, `window.parent`, `localStorage` shared with a host page outside the iframe's origin, or any other browser primitive that breaks under cross-origin embedding — except for `localStorage` keys scoped to the Hecate origin (which is the iframe's own origin), which IS allowed and SHALL be used for `access_token` / `refresh_token`.

#### Scenario: All assets resolve when loaded in an iframe

- **WHEN** `/embed/chat?agent=<uuid>` is loaded inside an `<iframe src="…/embed/chat?agent=<uuid>">` hosted on a different origin
- **THEN** all CSS, fonts, and JavaScript bundles SHALL load without mixed-content or cross-origin errors

#### Scenario: Auth tokens stored under iframe origin

- **WHEN** the user is authenticated via the embed route
- **THEN** `access_token` and `refresh_token` SHALL be stored in `localStorage` of the iframe's document (Hecate origin), NOT in the host page's localStorage

### Requirement: Widget does NOT integrate with ChannelABC

The widget SHALL NOT register a channel adapter under `ChannelABC`, SHALL NOT use `CanonicalMessage`, and SHALL NOT route through the `Gateway.route()` entry point. All chat traffic SHALL flow directly from the browser to `/v1/chat/completions` and `/api/conversations`, bypassing the channel abstraction entirely. This is a deliberate scope decision for the simplified 11.2 delivery — the deferred "11.2 full" (anonymous to-C scenarios) is the future work that MAY revisit the channel abstraction if needed.

#### Scenario: No channel adapter registered for the widget

- **WHEN** the embed route is deployed
- **THEN** `PluginRegistry` SHALL NOT contain any channel adapter whose `name` matches the web widget (e.g., no `"web-widget"` entry)
- **THEN** `POST /v1/channels/web-widget/webhook` SHALL return `404 Not Found`

#### Scenario: Chat traffic bypasses Gateway

- **WHEN** the embed route issues `POST /v1/chat/completions`
- **THEN** the request SHALL be handled by the existing OpenAI-compatible handler (same as the dashboard chat), NOT by `Gateway.route()` and NOT by any `ChannelABC.receive()` implementation
