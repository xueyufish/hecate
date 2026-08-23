## ADDED Requirements

### Requirement: browser_navigate tool navigates to a URL
The system SHALL provide a `browser_navigate` built-in tool that accepts a required `url` string and an optional `wait_until` enum string (`load` | `domcontentloaded` | `networkidle`, default `load`), navigates the agent's per-session browser to that URL, and returns the final URL, page title, and HTTP status (or null when not applicable).

#### Scenario: Successful navigation
- **WHEN** `browser_navigate({"url": "https://example.com"})` is called and the URL is in the configured allow-list
- **THEN** the tool SHALL navigate the browser, return `{"url": "https://example.com/", "title": "...", "status": 200}`

#### Scenario: Navigation timeout
- **WHEN** `browser_navigate` is called and the target does not finish loading within `BROWSER_NAVIGATION_TIMEOUT` seconds (default 30s)
- **THEN** the tool SHALL return a result with `error: "navigation_timeout"` and a `partial_url` field showing the last URL the browser was on

#### Scenario: Navigation to non-allow-listed domain
- **WHEN** `browser_navigate` is called with a URL whose host is NOT in the agent environment's `allowedDomains`
- **THEN** the tool SHALL refuse the navigation without contacting the network, return a result with `error: "domain_not_allowed"` and the offending host
- **AND** the system SHALL emit an audit log entry tagged with `risk_level: "HIGH"`

#### Scenario: Browser session not initialized
- **WHEN** `browser_navigate` is the first browser tool called in a session
- **THEN** the tool SHALL lazily initialize a browser session for the current agent session (acquire a pooled sandbox container, launch Chromium with the configured headless mode, attach via CDP) before navigating

#### Scenario: Browser toolchain unavailable
- **WHEN** `browser_navigate` is called but Playwright is not installed in the runtime image or the sandbox container cannot be acquired
- **THEN** the tool SHALL return a result with `error: "browser_unavailable"` and a human-readable reason, without crashing the agent loop

### Requirement: browser_click tool clicks a page element
The system SHALL provide a `browser_click` built-in tool that accepts a `selector` string and optional `text` string and `index` integer (default 0). When `text` is provided the tool SHALL resolve the element by visible text first; otherwise it SHALL use `selector` and `index` to disambiguate when multiple matches exist.

#### Scenario: Click by CSS selector
- **WHEN** `browser_click({"selector": "button.submit"})` is called and exactly one matching element exists
- **THEN** the tool SHALL click that element and return `{"clicked": true, "selector": "button.submit"}`

#### Scenario: Click by visible text
- **WHEN** `browser_click({"text": "Confirm Purchase"})` is called and a button with that visible text exists on the page
- **THEN** the tool SHALL click the first matching element

#### Scenario: Element not found
- **WHEN** `browser_click` cannot resolve a unique element within `BROWSER_ACTION_TIMEOUT` seconds (default 5s)
- **THEN** the tool SHALL return a result with `error: "element_not_found"` and the selector/text that failed to resolve

#### Scenario: Multiple matches without disambiguation
- **WHEN** `browser_click({"selector": "li.item"})` matches more than one element and `index` is not provided
- **THEN** the tool SHALL return a result with `error: "ambiguous_selector"` and the count of matches

### Requirement: browser_type tool types text into an input
The system SHALL provide a `browser_type` built-in tool that accepts a required `selector` string and `text` string, and an optional `submit` boolean (default `false`). The tool SHALL focus the input element, clear its existing content, type the text character-by-character to trigger any input handlers, and optionally press Enter at the end when `submit` is true.

#### Scenario: Type into input field
- **WHEN** `browser_type({"selector": "input[name=q]", "text": "playwright python"})` is called
- **THEN** the tool SHALL focus the input, clear it, type "playwright python", and return `{"typed": true, "length": 17}`

#### Scenario: Type and submit
- **WHEN** `browser_type({"selector": "input[name=q]", "text": "python", "submit": true})` is called
- **THEN** the tool SHALL type "python" into the input and press Enter, returning `{"typed": true, "submitted": true}`

#### Scenario: Input field not found
- **WHEN** `browser_type` cannot resolve the selector to a fillable input
- **THEN** the tool SHALL return a result with `error: "element_not_found"` or `error: "element_not_fillable"` as appropriate

### Requirement: browser_extract tool extracts page content
The system SHALL provide a `browser_extract` built-in tool that accepts an optional `selector` string and an optional `mode` enum (`text` | `html` | `a11y`, default `a11y`). When `mode=a11y`, the tool SHALL return Playwright's accessibility tree as a serialized text representation. When `mode=text`, it SHALL return visible text. When `mode=html`, it SHALL return the element's outer HTML. When `selector` is omitted, the tool SHALL extract from the document root.

#### Scenario: Extract a11y tree of whole page
- **WHEN** `browser_extract({})` is called with no arguments
- **THEN** the tool SHALL return the full accessibility tree of the current page as a structured text representation including role, name, and state for each interactive element

#### Scenario: Extract text of a specific element
- **WHEN** `browser_extract({"selector": "article.main", "mode": "text"})` is called
- **THEN** the tool SHALL return the visible text content of the `<article class="main">` element

#### Scenario: Extract mode is constrained by risk level
- **WHEN** `browser_extract({"mode": "html"})` is called
- **THEN** the tool SHALL be tagged with `risk_level: "MEDIUM"` (HTML may contain PII) and its output SHALL pass through the existing Outbound DLP Engine (9.10)

### Requirement: browser_screenshot tool captures page screenshot
The system SHALL provide a `browser_screenshot` built-in tool that accepts an optional `full_page` boolean (default `false`) and an optional `selector` string. The tool SHALL capture a PNG screenshot and return it as a base64-encoded string with image dimensions and the current URL.

#### Scenario: Viewport screenshot
- **WHEN** `browser_screenshot({})` is called
- **THEN** the tool SHALL return `{"image_base64": "...", "width": 1280, "height": 720, "url": "..."}` containing the visible viewport

#### Scenario: Full page screenshot
- **WHEN** `browser_screenshot({"full_page": true})` is called
- **THEN** the tool SHALL capture and return the entire scrollable page content as a single PNG

#### Scenario: Element screenshot
- **WHEN** `browser_screenshot({"selector": "#login-form"})` is called
- **THEN** the tool SHALL return a PNG of the bounding box of the matched element

#### Scenario: Screenshot output passes through DLP
- **WHEN** `browser_screenshot` returns an image
- **THEN** the system SHALL pipe the image through the Outbound DLP Engine (9.10) recognizer set
- **AND** if PII is detected, the system SHALL redact the matched regions and return a redacted image with a `redactions_applied: int` field

### Requirement: browser_fill_form tool fills multiple form fields
The system SHALL provide a `browser_fill_form` built-in tool that accepts a required `fields` array, where each field has a `selector` string and `value` string. The tool SHALL fill all fields atomically and return per-field success status.

#### Scenario: Fill multi-field login form
- **WHEN** `browser_fill_form({"fields": [{"selector": "input[name=user]", "value": "alice"}, {"selector": "input[name=pass]", "value": "***"}]})` is called
- **THEN** the tool SHALL fill both inputs and return `{"filled": [{"selector": "input[name=user]", "ok": true}, {"selector": "input[name=pass]", "ok": true}]}`

#### Scenario: Partial failure
- **WHEN** one of the selectors does not match a fillable input
- **THEN** the tool SHALL return `{"filled": [{"selector": "...", "ok": true}, {"selector": "...", "ok": false, "error": "element_not_fillable"}], "partial": true}`

### Requirement: per-agent-session browser session lifecycle
The system SHALL lazily initialize exactly one browser session per agent session. The browser SHALL run inside a sandbox container acquired from the existing `SandboxPool`, attach via the Chrome DevTools Protocol over the Docker network, and SHALL be torn down when the agent session ends or when the sandbox pool retires the container per the existing `max_uses` policy.

#### Scenario: First browser tool call initializes session
- **WHEN** the first `browser_*` tool is called for a given agent session
- **THEN** the system SHALL acquire a sandbox container from `SandboxPool`, launch headless Chromium inside it, attach via CDP, and record the session in the existing audit pipeline (9.14)

#### Scenario: Subsequent browser tool calls reuse session
- **WHEN** the second and later `browser_*` tool calls occur within the same agent session
- **THEN** the system SHALL reuse the same browser session and CDP connection without re-acquiring a container

#### Scenario: Session end tears down browser
- **WHEN** the agent session ends (normal termination, error, or explicit close)
- **THEN** the system SHALL close the CDP connection, stop Chromium, and return the sandbox container to the pool for reuse per `SandboxPool` policy

#### Scenario: Container retired by max_uses policy
- **WHEN** the sandbox pool retires a container that was hosting the browser session (per `max_uses`)
- **THEN** the next `browser_*` tool call SHALL lazily re-initialize a fresh browser session on a new container without failing the agent

### Requirement: browser network egress policy enforcement
The system SHALL route all browser outbound HTTP/HTTPS requests through the existing `NetworkPolicy` enforcement point (9.12). When the agent environment has no `allowedDomains` configured, the system SHALL treat the browser as having an empty allow-list and SHALL refuse all navigation and outbound fetches (fail-closed).

#### Scenario: Navigation to allow-listed domain succeeds
- **WHEN** the agent environment has `allowedDomains=["example.com"]` configured
- **THEN** navigation to `https://example.com/path` SHALL be permitted

#### Scenario: Navigation to non-allow-listed domain refused
- **WHEN** the agent environment has `allowedDomains=["example.com"]` configured
- **THEN** navigation to `https://evil.com/...` SHALL be refused by the policy layer before the request leaves the sandbox container
- **AND** an audit log entry tagged with `risk_level: "HIGH"` SHALL be emitted

#### Scenario: Empty allow-list blocks all egress
- **WHEN** the agent environment has no `allowedDomains` configured (default)
- **THEN** every `browser_navigate` call SHALL return `error: "domain_not_allowed"` and no network request SHALL be issued

### Requirement: browser tool risk gating and audit
The system SHALL classify each browser tool call with a `risk_level` of `MEDIUM` by default. The system SHALL upgrade `browser_navigate` to `HIGH` when the target host is outside `allowedDomains`. All browser tool invocations and results SHALL flow through the existing `PreToolHook` / `PostToolHook` / `ApprovalCallback` / `ToolDecisionModel` audit pipeline (9.4 + 9.14).

#### Scenario: MEDIUM risk tool invokes ApprovalCallback when configured
- **WHEN** the agent's tool policy requires approval for `MEDIUM` risk tools
- **THEN** the `ApprovalCallback` SHALL fire before tool execution

#### Scenario: HIGH risk navigation invokes ApprovalCallback
- **WHEN** `browser_navigate` is upgraded to `HIGH` due to a non-allow-listed domain
- **THEN** the `ApprovalCallback` SHALL fire before the network request is issued

#### Scenario: Audit pipeline records every browser tool call
- **WHEN** any `browser_*` tool completes (success or failure)
- **THEN** the system SHALL persist a `ToolDecisionModel` row with `tool_name`, `args`, `risk_level`, `result_summary`, and any `error` field

### Requirement: browser tool fallback when sandbox is disabled
The system SHALL refuse to execute any `browser_*` tool with an explicit `error: "browser_disabled"` when the runtime is configured with `AGENT_ENV_BACKEND=local` (i.e., sandbox enforcement is off), without attempting to launch a browser on the host.

#### Scenario: Local environment refuses browser tools
- **WHEN** any `browser_*` tool is called and `AGENT_ENV_BACKEND=local`
- **THEN** the tool SHALL return `{"error": "browser_disabled", "reason": "sandbox_required"}`
- **AND** no browser process SHALL be launched on the host
