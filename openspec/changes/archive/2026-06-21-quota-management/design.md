## Context

Hecate's cost governance stack currently has observability (Cost Dashboard 8.3 — see what was spent) and alerting (Alerting 8.6 — get notified on threshold breaches), but no enforcement layer. A workspace can consume unlimited resources with no hard cap. The existing rate limiter (`core/rate_limit.py`) provides only ephemeral per-API-key RPM limiting — it doesn't persist across restarts, doesn't track tokens or cost, and has no per-workspace scoping.

Competitive analysis across OpenAI, LiteLLM, Coze, Dify, Anthropic, and AWS Bedrock shows that quota management is a fundamental enterprise expectation. Key patterns: OpenAI uses tier-based RPM/TPM/monthly-spend with pre-check token reservation; LiteLLM uses hierarchical org→team→user→key budgets with Redis hot-path counters; Coze uses a unified "points" currency with pre-deduction; Dify uses workspace-level quotas with separate knowledge-base rate limits.

Hecate's existing infrastructure provides a strong foundation: AuthContext resolves workspace_id from JWT/API key; TraceModel records token usage per LLM call; CostService aggregates costs; Alerting provides the notification pipeline. This change adds the enforcement layer on top of these.

Key constraints: No Redis in the current stack (avoid adding infrastructure dependency for an S-effort feature). PostgreSQL is the primary datastore. The existing in-memory RateLimiter must be upgraded, not broken. All enforcement must respect workspace-level isolation.

## Goals / Non-Goals

**Goals:**
- Three resource types: requests (RPM + daily), tokens (daily + monthly), cost (monthly).
- Two scope levels: workspace (budget enforcement) and API key (rate limiting).
- Post-check enforcement: record actual token/cost usage after LLM calls, with middleware pre-check for request counts.
- Hard reject (429) for exceeded hard limits, with configurable soft limits that trigger Alerting (8.6).
- Three window types: rolling 60s (RPM), fixed daily (UTC midnight reset), fixed monthly (1st of month).
- Standard response headers (`X-Quota-Limit`, `X-Quota-Remaining`, `X-Quota-Reset`) for client-side rate awareness.
- Period auto-reset: daily and monthly usage records automatically roll over at window boundaries.

**Non-Goals:**
- Redis-backed counters — the in-memory + database hybrid is sufficient for v1. Redis can be added later for high-scale deployments.
- Pre-check token reservation (OpenAI pattern) — requires estimating output tokens before the call, adding complexity. v1 uses post-check (record actual usage after LLM call).
- Storage quotas (knowledge base GB) — deferred to a future iteration. Requires scanning file storage, which is a separate enforcement point.
- User-level quotas — only workspace and API key levels in v1. User-level adds resolution complexity (which user for API key calls?).
- Tier-based automatic quota assignment (OpenAI tier system) — quotas are manually configured per workspace in v1.
- Overage billing / graceful model downgrade — hard reject or soft allow only. No automatic routing to cheaper models.

## Decisions

### D1: In-memory + database hybrid over Redis

The quota system uses in-memory sliding windows for RPM (short-burst protection) and database-backed counters for daily/monthly usage (long-term budget enforcement). No Redis dependency.

**Alternatives considered:**
- **Redis-only** (LiteLLM pattern): Atomic increments, high performance, purpose-built for rate limiting. But adds a required infrastructure dependency (Redis server), which Hecate doesn't currently use. For an S-effort feature, the dependency cost is unjustified — most Hecate deployments won't have enough traffic for Redis to matter.
- **Database-only**: Fully persistent, no new deps. But every request hitting the database for a quota check adds latency and load on the hot path. Unacceptable for RPM checks (60 checks/min/key at 60 RPM).

**Rationale:** The hybrid matches what the existing `RateLimiter` already does (in-memory for RPM) while adding persistent tracking for daily/monthly quotas. The in-memory RPM limiter is per-process, which means multi-worker deployments each get their own counter — this slightly loosens the effective RPM (N workers × configured RPM) but is acceptable for v1. The database-backed daily/monthly counters are atomically incremented after each LLM call, which is a cold path (not every HTTP request, only LLM completions).

### D2: Post-check enforcement over pre-check token reservation

Token and cost quotas are checked after the LLM call completes (post-check), not before (pre-check with estimated token reservation).

**Alternatives considered:**
- **Pre-check with reservation** (OpenAI pattern): Reserve `estimated_input_tokens + max_output_tokens` before the call. If reservation would exceed quota, reject immediately. Prevents any overage. But requires accurate estimation of input tokens (tokenizer-dependent) and a `max_output_tokens` config that users may not set. Adds complexity and a tokenizer dependency.
- **Hybrid pre+post**: Reserve estimate pre-call, reconcile actual post-call. Best control but most complex.

**Rationale:** Post-check is simpler, doesn't require a tokenizer, and the brief overage window (one extra LLM call) is acceptable with the soft-limit Alerting integration as backstop. For request-count quotas (RPM, daily requests), the middleware does pre-check (reject before processing) because the count is known upfront. The asymmetry is intentional: requests are countable upfront, tokens/cost are only known after.

### D3: Fixed calendar windows for daily/monthly over sliding windows

Daily quotas reset at UTC midnight, monthly quotas reset on the 1st of each month (UTC).

**Alternatives considered:**
- **Sliding windows** (rolling 24h, rolling 30d): Smoother usage patterns, no burst-at-boundary. But expensive to compute (need to query all usage in the trailing window for every check) and harder to reason about ("how much budget do I have left this month?"). The database query cost scales with traffic.
- **Per-workspace custom reset day**: Some billing systems let you pick a custom billing-cycle start date (e.g., the 15th of each month). Adds configuration complexity for minimal v1 benefit.

**Rationale:** Fixed calendar windows are the industry standard for billing (LiteLLM, OpenAI monthly caps, Coze subscriptions all use calendar resets). They're cheap to check (just filter by `period_start <= now < period_end`) and intuitive for users ("my monthly budget resets on the 1st"). The burst-at-boundary problem is mitigated by the concurrent RPM limit.

### D4: Hard reject + soft limit dual mode per quota

Each quota definition has both a `limit_value` (hard cap — triggers 429) and an optional `soft_limit` (warning threshold — triggers Alerting).

**Alternatives considered:**
- **Hard reject only**: Simple, but no early warning. Users discover they're over budget only when requests start failing.
- **Soft allow only**: Too permissive — no way to actually cap runaway usage.
- **Two separate quota entries**: One for hard, one for soft. More flexible but doubles the model count and management overhead.

**Rationale:** The dual-mode is what LiteLLM does (`max_budget` + `soft_budget`). It's the best UX: soft limit gives advance warning via Alerting (8.6 integration), hard limit provides the actual enforcement backstop. Both are on the same QuotaModel row, so no extra tables. Default: soft_limit = 80% of limit_value.

### D5: Workspace + API Key two-level scoping

Quotas are defined at two levels: workspace (budget enforcement for the whole team) and API key (per-key rate limiting). No org-level or user-level quotas in v1.

**Alternatives considered:**
- **Five-level hierarchy** (LiteLLM pattern: org → team → user → key → end-user): Maximum flexibility, but Hecate doesn't have a team concept (only org → workspace → user), and the resolution logic for "which quota applies?" becomes complex with inheritance and overrides. Unjustified for v1 where most deployments have simple workspace-level budgeting.
- **Workspace-only**: Simplest. But loses per-API-key RPM limiting (which the existing RateLimiter provides). API keys are used to distinguish applications or environments within a workspace — different apps may need different rate limits.

**Rationale:** Workspace-level covers the budget use case ("this workspace gets $1000/month"). API-key-level covers the rate-limiting use case ("this key gets 100 RPM"). Together they cover the two primary scenarios. The QuotaModel.scope field (`workspace` or `api_key`) determines which dimension the quota applies to. Future iterations can add org-level and user-level if needed.

### D6: Middleware for request-count, dependency for resource-intensive endpoints

RPM and daily-request quotas are enforced in a FastAPI middleware (applies to all requests, after auth). Token and cost quotas are enforced via a FastAPI dependency on LLM-invoking endpoints only (chat completions, agent execution).

**Alternatives considered:**
- **Middleware-only**: Token/cost checks in middleware would need to run before the route handler but after the LLM call — impossible, because the LLM call happens inside the handler. Middleware can only do pre-checks.
- **Dependency-only**: Every route would need the dependency. Easy to forget on new routes. RPM limiting should be universal.

**Rationale:** The two-layer approach uses each tool appropriately: middleware is universal (all requests get RPM-checked), dependencies are targeted (only LLM endpoints get token/cost quota checks). The middleware resolves AuthContext, checks workspace-level RPM, then passes through. The LLM endpoint dependency checks workspace-level daily/monthly token and cost quotas before processing.

## Risks / Trade-offs

- **[Multi-worker RPM drift]** In-memory RPM counters are per-process. With N workers, effective RPM is N × configured RPM. → Acceptable for v1. Document the multiplier. For strict enforcement, add Redis in a future iteration. Most enterprise deployments use 1-2 workers behind a load balancer.

- **[Post-check overage window]** A workspace can exceed its monthly cost quota by one LLM call before being blocked. → Mitigated by soft_limit (80% threshold triggers Alerting). The maximum overage is bounded by the cost of a single LLM call (typically < $1). Acceptable for v1.

- **[Database write on every LLM call]** Each LLM completion triggers a quota usage increment (UPDATE QuotaUsageModel). → Acceptable: LLM calls are inherently slow (1-30 seconds), so an additional ~1ms DB write is negligible. For extreme scale, batch the increments.

- **[Quota check latency]** The middleware adds a database query to every request (checking workspace RPM quota existence). → Mitigated by caching quota definitions in memory (TTL 60s) after first load. The cache is per-process and refreshed periodically. Quota definition changes propagate within 60s.

- **[Period reset race]** At UTC midnight, multiple requests may race to reset the daily usage record. → Mitigated by using `INSERT ... ON CONFLICT DO UPDATE` (upsert) for period resets. The first request to detect an expired period creates a new record; concurrent requests find the new record.
