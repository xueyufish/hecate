# Browser Automation (6.27)

Hecate exposes six LLM-facing browser tools — `browser_navigate`, `browser_click`, `browser_type`, `browser_extract`, `browser_screenshot`, `browser_fill_form` — that let agents drive a real headless Chromium inside a sandboxed Docker container. They share the existing `SandboxPool` infrastructure (9.4c) so each agent session gets a private, isolated browser.

## When to enable

Enable browser tools when an agent needs to interact with dynamic, JavaScript-rendered web pages that `web_search` cannot reach — logins, dashboards, form submission, paginated content, screenshot inspection.

Do **not** enable for purely static scraping; `web_search` is cheaper.

## Prerequisites

- `AGENT_ENV_BACKEND=docker` (or anything other than `local`); the browser lives inside a Docker container, so a Docker daemon is required at runtime
- The `hecate-browser-sandbox` image built locally

## Building the image

The browser sandbox image (`hecate-browser-sandbox:latest`) bundles Python 3.12 + Playwright SDK + Chromium + a small HTTP driver that the main process invokes via `docker exec curl`. It is intentionally separate from the generic `hecate-sandbox` image used by `execute_code` so deployments that don't need browser tools don't pay the ~600MB Chromium footprint.

```bash
# One-off build via Docker Compose profile
docker compose -f docker/docker-compose.yml build hecate-browser-sandbox --profile browser

# Or directly via docker build
docker build -t hecate-browser-sandbox:latest docker/sandbox/
```

CI runs a `docker-sandbox-smoke` job on every PR that builds the image and asserts:

- Build succeeds with no manual intervention
- Resulting image is ≤ 700MB (the design budget from the catalog)
- The in-image entrypoint module imports cleanly

## Configuring per-environment network policy

Browser tools inherit the existing `NetworkPolicy` (9.12). Each agent environment has an `allowed_domains` field; if it is empty, every `browser_navigate` is refused (`domain_not_allowed` error) and no network request leaves the sandbox container. This is the **fail-closed default** for security.

To grant access, populate `allowed_domains` on the agent environment. Patterns support exact matches and `*.` wildcards:

```yaml
# Inside the agent environment record
allowed_domains:
  - example.com
  - "*.internal-portal.corp"
  - api.my-vendor.com
```

When `browser_navigate` is called with a URL whose host is not in the allow-list, the tool returns `{"error": "domain_not_allowed", "url": "...", "risk_level": "HIGH"}` and emits an audit row tagged `risk_level: HIGH`. The HIGH upgrade triggers `ApprovalCallback` per the existing 9.4 risk-gating path.

The other five browser tools (`browser_click`, `browser_type`, `browser_extract`, `browser_screenshot`, `browser_fill_form`) operate against the page currently loaded in the session and therefore inherit the navigation's allow-list check transitively.

## Tool reference

All six tools share the same session lifecycle: the **first `browser_*` call for an agent session** lazily allocates a sandbox container and launches headless Chromium inside it; subsequent calls reuse that browser until the session ends (or the pool retires the container per its `max_uses` policy).

| Tool | Parameters | Default risk | Notes |
|---|---|---|---|
| `browser_navigate` | `url` (required), `wait_until` (`load` \| `domcontentloaded` \| `networkidle`) | `MEDIUM` (upgraded to `HIGH` for non-allow-listed domains) | Refuses `domain_not_allowed` without contacting the network |
| `browser_click` | `selector` OR `text`, optional `index` | `MEDIUM` | `text` matches visible text; `index` disambiguates when multiple matches |
| `browser_type` | `selector`, `text`, optional `submit` | `MEDIUM` | `submit=true` presses Enter after typing |
| `browser_extract` | optional `selector`, optional `mode` (`a11y` \| `text` \| `html`) | `MEDIUM` | Default mode `a11y` returns Playwright's accessibility tree (LLM-friendly structured text). Output passes through the DLP engine. |
| `browser_screenshot` | optional `full_page`, optional `selector` | `MEDIUM` | Returns base64 PNG. Passes through DLP; PII is redacted in-place. |
| `browser_fill_form` | `fields: [{selector, value}, …]` | `MEDIUM` | Atomic fill; per-field failures reported, never aborts mid-batch |

`browser_extract` defaults to **a11y tree output**, not screenshots. Vision models are not required to consume browser state. Call `browser_screenshot` explicitly when visual inspection is necessary.

## Headless vs headful

v1 is **headless only**. The `--headless` flag is hard-coded in the Chromium launch arguments and no VNC/Xvfb viewer is exposed. Adding a noVNC viewer for visual debugging is a P4 follow-up; the trigger condition is the first customer that explicitly requires live browser observation.

## Comparison with `execute_code`

| Concern | `execute_code` | `browser_*` |
|---|---|---|
| Sandbox image | `hecate-sandbox` (lightweight) | `hecate-browser-sandbox` (~600MB Chromium) |
| Default network | `none` (no egress) | `bridge` + per-env allow-list |
| Session lifetime | One call, fire-and-forget | Per-agent-session, persistent |
| Output | stdout/stderr/exit_code | Structured JSON (URL/title, click status, screenshot bytes, etc.) |
| Risk level | `MEDIUM` | `MEDIUM` (upgraded to `HIGH` for non-allow-listed nav) |
| Approval flow | `MEDIUM` triggers `ApprovalCallback` when agent config demands | Same — risk gating is uniform |

## Common error codes

| Error | Cause | Resolution |
|---|---|---|
| `browser_disabled` | `AGENT_ENV_BACKEND=local` or no session manager wired | Switch to docker backend; ensure `BrowserSessionManager` is constructed at app startup |
| `browser_unavailable` | Playwright or Chromium not installed in the runtime image | Rebuild `hecate-browser-sandbox` image; verify `playwright install chromium` runs successfully during build |
| `domain_not_allowed` | Target host not in `allowedDomains` | Add the host to the agent environment's `allowedDomains` (exact or `*.` wildcard) |
| `element_not_found` | Selector/text didn't match within `BROWSER_ACTION_TIMEOUT` (5s default) | Re-issue with a more specific selector or a `text` argument; verify the page loaded the expected content |
| `ambiguous_selector` | Multiple elements matched and `index` was not provided | Pass `index` or refine the `text` argument to a more specific substring |
| `navigation_timeout` | Page didn't reach `wait_until` state within `BROWSER_NAVIGATION_TIMEOUT` (30s default) | Retry; some sites genuinely exceed 30s — raise the timeout in the agent's config or use `wait_until: "domcontentloaded"` |
| `driver_timeout` | In-container HTTP driver didn't respond within 60s | Check the container's `health` via `docker exec <id> curl http://127.0.0.1:8080/healthz`; if unhealthy, retire the container via `SandboxPool` recycle |
| `session_closed` | Tool called after session was closed | Browser session was torn down (normal session end or container retirement); re-issue with a new agent session |

## Manual verification (P3 close-out smoke test)

After deploying, run a manual E2E in any agent environment:

```bash
# In an agent run, ask the LLM to:
# 1. Navigate to https://example.com
# 2. Extract the page content (a11y mode by default)
# 3. Take a screenshot
# Expected: 3 successful tool calls, audit log has 3 rows with risk_level=MEDIUM
```

If any of the three calls returns `domain_not_allowed` for `example.com`, the environment's `allowedDomains` is empty — add `example.com` to it and retry.

## Operational notes

- Each browser session is one container. The default `SandboxPool` size (3) supports up to 3 concurrent browser sessions per main process. Raise `SANDBOX_POOL_SIZE` for higher concurrency.
- Containers are retired after `max_uses` invocations (default 50) regardless of session state. The browser session manager transparently re-initializes on the next call.
- Network egress is filtered by the `NetworkPolicy.apply_to_container` step (iptables injection) which requires `NET_ADMIN` capability. If the container was started without it, iptables injection fails silently and the in-driver `is_url_allowed` check still gates egress at the code level — fail-closed is preserved.

## P4 follow-ups (deferred, not in scope for 6.27)

- `browser_evaluate` — let the agent run arbitrary JS in the page (custom extraction)
- `browser_console_messages`, `browser_press_key`, `browser_select_option`, `browser_tabs` — finish aligning with the Microsoft Playwright MCP tool surface
- Headful mode with noVNC viewer — triggered by the first explicit customer demand for visual debugging
- Cloud browser providers (Browserbase, Steel.dev) — local sandbox is sufficient for v1
- 6.27a computer-use (OS-level GUI automation) — split out, lives in P4
