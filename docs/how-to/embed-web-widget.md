# Embed the Hecate Web Widget

This guide shows how to embed the Hecate chat widget into an Hecate portal page or a customer intranet page. The Web Widget (Simplified) is the 11.2 Wave 1 delivery — it is an iframe-embeddable chat component that reuses the existing employee JWT auth and the existing `/v1/chat/completions` streaming endpoint.

For the architectural rationale, see [ADR-031: Web Widget Iframe Architecture](../design/adr/031-web-widget-iframe-architecture.md). For the scope boundary (Simplified vs Full), see [ADR-031 § Scope boundary](../design/adr/031-web-widget-iframe-architecture.md#scope-boundary).

## Prerequisites

- A running Hecate deployment reachable from the host page.
- The Hecate control plane must allow the host page's origin as a frame ancestor (i.e., the X-Frame-Options or CSP `frame-ancestors` directives must include the host origin). For the standard Hecate deployment, the iframe loader accepts any origin; tighten this in production if the widget is only intended for known hosts.
- An agent UUID to pin the widget to. The widget always targets a single agent per embed page.
- The end user must have a Hecate session (employee JWT) in the iframe's origin. The widget does not handle anonymous onboarding — see [ADR-031 § Scope boundary](../design/adr/031-web-widget-iframe-architecture.md#scope-boundary) for the deferred anonymous scope.

## Embed snippet

```html
<iframe
  src="https://<hecate-host>/embed/chat?agent=<agent-uuid>"
  width="420"
  height="640"
  style="border: 0; position: fixed; bottom: 1rem; right: 1rem; z-index: 50;"
  title="Hecate Chat"
></iframe>
```

Replace `<hecate-host>` with the Hecate deployment origin (e.g., `https://hecate.example.com`) and `<agent-uuid>` with the target agent's UUID.

## What the widget does

- Defaults to a collapsed bubble anchored to the bottom-right corner of the iframe viewport.
- Clicking the bubble expands it into a 24rem × 36rem chat window.
- On first mount, the widget auto-creates a new conversation via `POST /api/conversations` with the configured `agent_id`.
- User messages are streamed via `POST /v1/chat/completions` with `session_id=conversationId` (same path as the dashboard chat).
- A close button in the chat window's top-right corner collapses the window back to the bubble. Closing does not persist — reloading the page re-creates the conversation.
- The "New Chat" button in the chat header creates a new conversation and clears the local message state.

## What the widget does NOT do

- It does not handle anonymous users. All widget users must be authenticated Hecate employees.
- It does not validate the host page's origin. The deferred 11.2 full scope (P5) will add `Origin` allowlisting.
- It does not inject via `<script>`. The host page must render the `<iframe>` element directly.
- It does not include internationalized strings. The widget UI is English-only in this version.

## Troubleshooting

### The iframe never expands

The browser may be blocking cookies or `localStorage` for the iframe's origin. Check the browser's site settings for `<hecate-host>` and ensure third-party cookies / site data is allowed.

### The widget shows "No agent specified"

The `?agent=<agent-uuid>` query parameter is missing or empty. Confirm the URL in the `src` attribute.

### The widget redirects to `/login`

The user does not have a Hecate session, or the access token has expired. The widget embeds Hecate's existing auth flow:

- If the user has never logged in, the `<AuthGuard>` redirects to `/login` inside the iframe.
- If the access token has expired, the next API call returns `401`, and the `api-client` interceptor clears the local tokens and redirects to `/login`.

In both cases, the host page sees the iframe suddenly switch to the Hecate login page. The 11.2 simplified scope accepts this experience; the deferred 11.2 full scope will provide a more compact re-authentication UI.

### The widget streams nothing

Confirm the agent UUID is valid and the user has access to it. Check the browser's network tab for the `POST /v1/chat/completions` request and the corresponding `POST /api/conversations` request on first load.

## Multi-agent embedding

To embed multiple agents on the same host page, render one iframe per agent with a different `?agent=` value:

```html
<iframe src="https://<hecate-host>/embed/chat?agent=<agent-1-uuid>" width="420" height="640" ...></iframe>
<iframe src="https://<hecate-host>/embed/chat?agent=<agent-2-uuid>" width="420" height="640" ...></iframe>
```

Each iframe maintains its own conversation state and its own collapsed / expanded state.

## For Hecate operators

The widget is a route in the Next.js dashboard project (`/embed/chat`), not a separate deployment. No new infrastructure is required — the iframe is served by the same Next.js process that serves the dashboard.

Widget-created conversations appear in the existing conversation history alongside dashboard chat conversations. The 11.2 simplified scope does not tag widget conversations with a separate `source_channel`; if you need to distinguish them, the deployment can configure the analytics pipeline to filter by the iframe's `Referer` header (the value will contain `/embed/chat` for widget traffic).
