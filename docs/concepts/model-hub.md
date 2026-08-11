# Model Hub

An agent platform that locks you into one LLM vendor is a strategic liability — models improve at different rates, prices change monthly, and the best model for a summarization task is rarely the best for code generation. Hecate's **Model Hub** is the layer that keeps you un-locked: every LLM call — regardless of provider — flows through one unified service that routes, falls back, and tracks cost.

The Model Hub is built on [LiteLLM](https://github.com/BerriAI/litellm), which means **100+ providers** work out of the box: OpenAI, Anthropic, Google, DeepSeek, Alibaba Qwen, Zhipu GLM, Groq, Together, OpenRouter, Ollama (local), and any OpenAI-compatible endpoint. This page explains the routing model, the resilience layer, and the two configuration paths. For env-var-by-env-var setup, see the [Configure LLM Providers guide](../how-to/configure-llm-providers.md).

---

## The mental model

Every LLM call in Hecate enters through one method on the engine's `EnginePort`:

```
Worker (conversation / agent node)
    │
    ▼
EnginePort.llm_invoke(messages, config)         engine/ports.py
    │
    ▼
LLMService                                      services/llm/service.py
    │
    ├── Routing? ──► ModelRouter.select_model() │ picks model by strategy
    │                                            │ (when routing config set)
    ├── Invoke ────► LiteLLM router             │ the actual provider call
    │
    ├── Failure? ──► Fallback chain             │ try fallback_models in order
    │
    └── Provider down? ► CircuitBreakerManager  │ per-provider breaker trips
```

The `LLMService` (`services/llm/service.py`) is the single chokepoint. Whether you're running `chat` mode with one model or a complex `workflow` with per-node models, every call passes through this service and gets routing, fallback, and circuit-breaking for free.

---

## Two configuration paths

| Path | Where stored | Best for |
|------|-------------|----------|
| **Environment variables** | `.env` file | Quick setup, single-provider, dev/test |
| **Database-backed providers** | PostgreSQL (encrypted) | Multi-tenant, runtime management, enable/disable without restart |

Both paths work simultaneously. Env vars are the LiteLLM fallback for any model not in the DB registry. DB-registered providers feed the model registry; agents that reference a registered model get availability checks and provider-level config (timeout, retries). See the [Configure LLM Providers guide](../how-to/configure-llm-providers.md) for the full provider table and env var names.

---

## Routing strategies

When an agent or workflow node specifies a **routing config** instead of a fixed model, the `ModelRouter` (`services/llm/routing.py`) selects the best model at call time:

| Strategy | Behavior | Use case |
|----------|----------|----------|
| `COST` | Cheapest model meeting constraints | Cost-sensitive workloads |
| `LATENCY` | Fastest model | Real-time applications |
| `CAPABILITY` | Most capable model | Complex reasoning |
| `BALANCED` | Weighted score across cost/latency/capability | Default — general purpose |

Routing is **opt-in**. If you set a fixed `model` on a node or agent, the router is bypassed and that exact model is called. Routing kicks in only when you provide a `RoutingConfig` with a strategy and a candidate model pool.

---

## Resilience: fallback and circuit breaking

Two layers keep LLM traffic flowing when a provider fails:

### Fallback chain

`LLMService` accepts an ordered `fallback_models` list. If the primary model call raises, the service tries each fallback in order (`_try_fallback` for regular calls, `_try_fallback_stream` for streaming). Both the primary attempt and each fallback are logged so you can see which model ultimately served the request.

### Per-provider circuit breaker

`CircuitBreakerManager` (`services/llm/circuit_breaker.py`) tracks failure rates **per provider prefix** (e.g., `deepseek/`, `anthropic/`, `zhipu/`). When a provider's failure rate crosses the threshold, the breaker trips and subsequent calls to that provider skip immediately — no waiting for a timeout — and go straight to fallback. The breaker resets after a cooldown period, allowing the provider to recover.

This means a partial outage of one provider doesn't cascade into request timeouts across your fleet. The circuit breaker is what turns "DeepSeek is down" from a 30-second hang into a sub-millisecond redirect to OpenAI.

---

## Progressive delivery: A/B testing and gray release

For controlled model rollouts, the Model Hub provides two mechanisms:

| Mechanism | Class | What it does |
|-----------|-------|-------------|
| **A/B testing** | `ABTestManager` (`services/llm/ab_testing.py`) | Splits traffic between model variants, collects outcomes, computes statistical significance |
| **Gray release** | `GrayReleaseManager` (`services/llm/gray_release.py`) | Progressive rollout — route 5% → 25% → 50% → 100% of traffic to a new model over a configured schedule |

Use A/B testing to decide *whether* a new model is better. Use gray release to roll it out *safely*. Both layer on top of the routing and fallback infrastructure — a gray-released model that starts failing trips the circuit breaker and falls back just like any other.

---

## Tool calling

The Model Hub handles the LLM-side of [tool integration](tools-and-mcp.md). `services/llm/tool_calling.py` provides four helpers:

- `format_tools_for_llm()` — converts Hecate tool definitions to the LLM provider's function-calling schema
- `parse_tool_calls()` — extracts tool invocations from the LLM response
- `create_tool_result_message()` — wraps tool output for the next LLM turn
- `inject_tool_results()` — feeds tool results back into the conversation

This is what makes tool-calling work uniformly across providers, even though each provider has a slightly different function-calling API surface.

---

## How cost flows out

Every `LLMService` call records token usage (input + output), which feeds the [cost tracking](observability.md#metrics) infrastructure: per-request cost, per-agent cost, per-workspace cost, and budget enforcement. Routing decisions made by `ModelRouter` are therefore visible downstream — if `COST` strategy picked `gpt-4o-mini` over `gpt-4o`, the savings show up in the cost dashboard.

---

## Choosing what to configure

| You want to... | Use |
|----------------|-----|
| Quick-start with one provider | Env vars — `OPENAI_API_KEY` and you're running |
| Manage providers at runtime without restart | DB-backed provider registry via `/api/model-providers` |
| Always call the same model | Fixed `model` on the agent or node — no routing |
| Pick the cheapest / fastest / most capable model per call | `RoutingConfig` with the appropriate strategy |
| Survive a provider outage | `fallback_models` list + circuit breaker (automatic) |
| Test whether a new model is better | `ABTestManager` with statistical significance |
| Roll out a new model gradually | `GrayReleaseManager` with a progressive schedule |
| Call local models | Ollama via `ollama/` prefix — no API key |

---

## Further reading

- [Configure LLM Providers](../how-to/configure-llm-providers.md) — env vars, provider prefix table, DB registry setup
- [Agents and Execution Modes](agents.md) — where `model` config lives on an agent
- [Observability](observability.md) — how token usage and cost metrics flow from LLM calls
- [Extension Points](../reference/extension-points.md) — the `EnginePort.llm_invoke` method signature
- [Model Hub Design](../design/model-hub-design.md) — full L2 breakdown: catalog, lifecycle, governance, fine-tuning, cost budgets
- [ADR-022: Model Hub Enhancement](../design/adr/022-model-hub-enhancement.md) — architecture decisions for the routing and resilience layer
