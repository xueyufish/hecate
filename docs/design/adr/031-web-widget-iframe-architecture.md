# ADR-031: Web Widget Iframe Architecture (Bypassing ChannelABC)

## Status

Accepted (2026-08-16, Multi-Channel Wave 1 close-out; extends ADR-016 platform SPI; defers anonymous to-C scope to P5)

## Context

Hecate's Multi-Channel Wave 1 delivers 11.3 Feishu and 11.9 Slack via the `ChannelABC` platform SPI (ADR-016): each channel adapter wraps an IM platform's SDK, normalizes inbound events to `CanonicalMessage`, and routes through `WorkflowExecutionService.execute()`. The same wave had a third item — 11.2 Web Widget — but the original scope of "Web Widget = anything that talks to a chat frontend" landed on the wrong abstraction.

The catalog's 11.2 entry originally conflated two distinct scenarios:

- **Simplified (Wave 1, P3)**: Embed Hecate's chat UI into Hecate's own portal or a customer intranet portal. Users are already-authenticated Hecate employees. The client is a browser inside an `<iframe>`. The browser can already POST directly to `/v1/chat/completions` and stream SSE responses — no platform intermediary exists.
- **Full (deferred P5)**: Embed Hecate's chat into arbitrary third-party public websites via a `<script>` tag. Users are anonymous. The trust boundary is the Origin / Referer header, and a temporary JWT broker is required.

These two scenarios have different client models, different threat surfaces, and different scope. If we collapse them into one feature, the simplified version either gets the unnecessary burden of RS256 + Origin allowlist + WidgetModel, or the full version gets short-changed by the simplified version's "just use the existing JWT" shortcut.

11.3 Feishu and 11.9 Slack are *platform-mediated* channels: the IM platform owns the conversation thread, webhooks push events to us, and we respond asynchronously through the platform's API. `ChannelABC` is the right abstraction for that pattern. But the Web Widget's client is a browser, not a platform:

- The browser can open a direct HTTP connection to our backend.
- The browser can stream the SSE response.
- There is no third-party intermediary owning the conversation.
- The user is already authenticated against Hecate (in the simplified scope).

Treating the Web Widget as a `ChannelABC` adapter would force it through a webhook abstraction it doesn't need, inherit a `CanonicalMessage` model it doesn't fit, and require a fake identity-binding flow (the IM scenario's `IMIdentityBinding` is for "I am this Feishu user, link me to my Hecate account"; the browser scenario already has the JWT). The shape mismatches.

## Decision

The 11.2 Web Widget (Simplified) implementation bypasses `ChannelABC` and the entire channel / gateway / platform-SPI abstraction. The browser talks directly to the existing OpenAI-compatible endpoints.

### What this means concretely

- The widget route (`/embed/chat`) does not register a `ChannelABC` adapter under `PluginRegistry`. `POST /v1/channels/web-widget/webhook` returns `404 Not Found`.
- The browser issues `POST /v1/chat/completions` with `session_id=conversationId` and consumes the SSE stream, identical to the dashboard chat.
- The browser issues `POST /api/conversations` to auto-create a conversation on first load, identical to the dashboard chat's "New Chat" flow.
- Authentication reuses the existing employee JWT pipeline. `AuthGuard` wraps the route. The `api-client` 401 interceptor handles expiry, identical to the dashboard chat.
- No `CanonicalMessage`, no `ChannelCapabilities`, no `IMIdentityBinding`, no `MessageBus`, no `Gateway.route()` entry point.

### Why iframe + same Next.js project (not `<script>` loader.js)

The simplified scope renders inside an `<iframe>` on the host page. The iframe mode has three properties we want:

1. **Style isolation is automatic**: the host page's CSS does not leak into the widget, and our Tailwind preflight does not leak into the host page. This is the CSS-with-scope problem `<script>` loader mode has to solve separately (Shadow DOM / CSS Modules / namespace prefix).
2. **Origin isolation is automatic**: `localStorage`, `sessionStorage`, and cookies are scoped to the iframe's document origin (Hecate's origin), not the host page's origin. Each host site gets its own Hecate session.
3. **No new build pipeline**: the iframe content is a normal Next.js route, sharing the existing bundler, HMR, lint, and test infrastructure.

`<script src="...">` loader.js mode (the pattern Intercom and Dify use) corresponds to the deferred 11.2 full scope: arbitrary third-party websites, anonymous users, and the need to render across distinct host environments. That scope is single-purpose, requires its own bundle, and is out of scope here.

### Scope boundary

The ADR explicitly codifies the simplified-vs-full split:

- **Accepted (Wave 1, P3)**: iframe + employee JWT + reuse OpenAI-compatible endpoints + embed route in the same Next.js project.
- **Deferred (P5)**: `<script>` loader.js + anonymous to-C + temporary JWT + RS256 + Origin allowlist + WidgetModel + JS bundle. This is a separate ADR (most likely) and a separate implementation.

The 11.2 catalog entry wording — "embeddable for any Hecate deployment" — was misleading and should be read as "iframe-embeddable into Hecate or a customer's intranet, *not* script-embeddable on arbitrary public sites". The full version remains P5 deferred per the 2026-08-12 Multi-Channel scoping decision.

## Rationale

### Why not reuse `ChannelABC` (treating the browser as a pseudo-IM)

- IM channels have a webhook *receiver* and an outbound *sender*. The browser has neither: it issues requests and consumes streamed responses. Forcing it through a webhook-first model requires either (a) a fake "webhook" that is actually a fetch from inside the iframe (which only adds latency and a re-abstraction layer) or (b) inventing a new "synchronous channel" subclass that breaks the existing SPI contract.
- IM channels have platform-mediated identity. The browser has Hecate's own JWT. Layering an `IMIdentityBinding` flow on top of an existing JWT session duplicates the auth concept with a different shape.
- `CanonicalMessage` is designed for asynchronous webhook events with text / cards / file uploads. The widget's payload is a synchronous OpenAI-compatible request body. The shape mismatch is significant.

### Why not a separate `WidgetABC` SPI

- We don't have a second widget to reuse it for. The 11.2 full scope may have different requirements (Origin validation, RS256 token exchange) that don't naturally share a base class with the simplified version. Building an abstraction for one consumer is speculative.
- The current shape is "browser → existing API endpoint". Adding an abstraction layer between the browser and the endpoint costs code without adding flexibility.

### Why not `<script>` loader.js in the simplified scope

- CSS isolation is non-trivial; the iframe mode gives it for free.
- The host page's environment is unpredictable (custom DOM observers, micro-batched React renders, etc.) — the iframe isolates us from those.
- The simplified scope's users are Hecate employees who already have a Hecate session; the embed loader does not need to handle anonymous onboarding.
- The 11.2 full scope (anonymous to-C) will need the loader.js pattern, and that work should not be rushed into the simplified delivery.

### Why `ChatSurface` is a shared component, not a copy-paste

The dashboard chat (`web/src/app/(dashboard)/chat/[conversationId]/page.tsx`) and the embed chat share the entire conversation UI: message list, streaming render, kb / memory chips, queue indicator, New Chat button. Duplicating would mean:

- A bug fix in one place does not propagate to the other.
- New features (e.g., markdown rendering, citation display) need to be applied twice.
- Visual drift between the two surfaces is invisible until someone is comparing them side-by-side.

`ChatSurface` accepts a `mode: "dashboard" | "embed"` prop that controls only the surrounding chrome (sidebar offset, top header bar). The message-list rendering and streaming logic lives in one place.

## Consequences

### Easier

- The simplified version ships as a thin Next.js route with no backend changes, no new dependencies, no Alembic migration, and no changes to `ChannelABC`. Estimated work: S effort, matching the catalog's 11.2 entry.
- The dashboard chat and the embed chat share bugs and features through `ChatSurface`. A single test surface for both.
- The 11.2 full scope (anonymous to-C) can be designed independently with its own architecture, rather than being constrained by whatever the simplified version chose.

### Harder

- Anyone proposing to add a `ChannelABC` adapter for the widget in the future will see this ADR and understand why it's intentionally rejected. Future contributors won't waste cycles re-litigating the decision.
- The widget's auth pipeline is the same as the dashboard chat's auth pipeline. If we want widget-specific auth (e.g., a one-time token in the URL for a customer portal), it has to be added as a special-case layer on top of the JWT — the `ChannelABC` route would have given us a clean place to add it.
- The embed route and the dashboard chat route share the same `api-client` instance. If we want to differentiate behavior (e.g., rate-limit widget traffic separately), we need a different mechanism (e.g., a custom header) rather than going through a different SPI.

### Follow-up work

- Update the 11.2 catalog entry to clarify the simplified-vs-full scope boundary (handled in the change PR).
- The 11.2 full scope (P5 deferred) will need its own ADR when triggered. That ADR will likely describe a different architecture: `<script>` loader.js, JS bundle, anonymous token broker, RS256.

### Note on `channel: "web-widget"` in other ADRs

ADR-023 ([Tool Platform Enhancement](023-tool-platform-enhancement.md)) describes a `ToolPolicyLayer` DSL with a `channel` field that can match a string identifier (`channel: "web-widget"`) to filter tool availability per channel. This is a **separate dimension** from `ChannelABC`:

- The `channel` field in `ToolPolicyLayer` is a free-form string that downstream code matches against the request's source identifier. It is **not** a `ChannelABC` adapter name and does not require any adapter to be registered.
- The 11.2 Web Widget (Simplified) does not currently populate any channel identifier on its requests to `/v1/chat/completions` or `/api/conversations`. A `channel: "web-widget"` rule in `ToolPolicyLayer` would not match widget traffic today.
- If a future version wants to apply channel-specific tool policies to widget traffic (e.g., deny `admin_*` tools in the embed context), the implementation will need to inject a `channel` field into the request context from the embed route (e.g., a request header or a request body field). This is left as a follow-up enhancement, not part of the 11.2 simplified scope.

In short: ADR-023's `channel: "web-widget"` is illustrative and **does not** imply the widget integrates with `ChannelABC`.

## Cross-references

- [ADR-016](016-platform-spi-architecture.md) — Platform SPI Architecture (15 extension points, including `ChannelABC`). This ADR documents an explicit *non-participation* in that SPI for the Web Widget.
- [ADR-018](018-zero-trust-identity-architecture.md) — Zero Trust Identity Architecture. The widget inherits the existing JWT trust boundary; the 11.2 full scope will need a separate trust model.
- [`docs/features/feature-catalog.md`](../features/feature-catalog.md) — 11.2 entry wording adjustment.
- [`docs/features/roadmap.md`](../features/roadmap.md) — Multi-Channel Wave 1 close-out.
- [`docs/features/p3-mvp-audit.md`](../features/p3-mvp-audit.md) — 2026-08-12 multi-channel scoping decision.

## Embed usage snippet

```html
<!-- Embed Hecate chat into an intranet portal page. Replace <host> with the
     Hecate deployment origin and <agent-uuid> with the target agent id. -->
<iframe
  src="https://<host>/embed/chat?agent=<agent-uuid>"
  width="420"
  height="640"
  style="border: 0; position: fixed; bottom: 1rem; right: 1rem; z-index: 50;"
  title="Hecate Chat"
></iframe>
```

The iframe hosts the widget UI. The widget handles authentication via the user's existing Hecate session (or redirects to `/login` inside the iframe when the token is missing or expired). The host page does not need to manage Hecate session state.
