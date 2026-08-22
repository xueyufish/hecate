# How to Develop Custom Extensions

Hecate's engine defines 26 [extension interfaces](../reference/extension-points.md) as abstract contracts, plus 8 plugin SPI types. Every one of them ships with a default implementation — but the whole point of an extension point is that you can replace it with your own. This guide walks through the pattern and shows three concrete examples.

> For the full interface catalog and method signatures, see [Extension Points Reference](../reference/extension-points.md). This page focuses on *how* to implement and wire custom extensions.

---

## The Ports and Adapters pattern

Hecate's engine layer has **zero external dependencies** (the sole exception is `jsonschema` for DSL validation). It achieves this through the Ports and Adapters (Hexagonal Architecture) pattern:

```
┌─────────────────────────────────────────────┐
│  ENGINE (zero deps)                         │
│                                             │
│  Defines abstract interfaces:               │
│    EnginePort, Worker, CheckpointStore,     │
│    ContextEngine, SchedulerStrategy,        │
│    Guardrail Hooks, RetryStrategy, ...      │
│                                             │
│  Calls ONLY abstract methods.               │
└──────────────────┬──────────────────────────┘
                   │ implements
┌──────────────────▼──────────────────────────┐
│  SERVICES (your adapters)                   │
│                                             │
│  Concrete implementations:                  │
│    LLMService → implements llm_invoke       │
│    ToolRegistry → implements tool_execute   │
│    SessionStateMaterializer → save/load     │
│    YourCustomHook → implements PreLLMHook   │
└─────────────────────────────────────────────┘
```

To extend Hecate, you implement an abstract interface and inject your concrete class at runtime. The engine never imports your code — it calls the interface.

---

## Three-step recipe

Every extension follows the same three steps:

1. **Implement** — subclass the ABC and implement its abstract methods
2. **Wire** — inject your instance where the runtime expects it
3. **Test** — write a unit test using the engine's lightweight stub patterns

---

## Example 1: Custom CheckpointStore

**Goal**: Store checkpoint caches in Redis instead of PostgreSQL for faster state access. (Checkpoints are materialized caches of the event log — see [Log-as-Truth, ADR-030](../design/adr/030-event-sourced-execution-state.md) — so a Redis-backed store is a natural fit for the hot path.)

### Step 1 — Implement

```python
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis

from hecate.engine.checkpoint import CheckpointStore


class RedisCheckpointStore(CheckpointStore):
    """Redis-backed checkpoint store with TTL-based expiry."""

    def __init__(self, redis: Redis, ttl_seconds: int = 86400) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def save(self, state: dict) -> uuid.UUID:
        checkpoint_id = uuid.uuid4()
        key = f"hecate:checkpoint:{checkpoint_id}"
        payload = {
            "id": str(checkpoint_id),
            "state": json.dumps(state),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._redis.hset(key, mapping=payload)
        await self._redis.expire(key, self._ttl)
        return checkpoint_id

    async def load(self, checkpoint_id: uuid.UUID) -> dict:
        key = f"hecate:checkpoint:{checkpoint_id}"
        raw = await self._redis.hget(key, "state")
        if raw is None:
            raise KeyError(f"Checkpoint {checkpoint_id} not found")
        return json.loads(raw)

    async def list_checkpoints(
        self, session_id: uuid.UUID | None = None
    ) -> list[dict]:
        pattern = "hecate:checkpoint:*"
        keys = await self._redis.keys(pattern)
        results = []
        for key in keys:
            data = await self._redis.hgetall(key)
            results.append({"id": data["id"], "created_at": data["created_at"]})
        return results
```

### Step 2 — Wire

Pass your store to the runtime configuration. The wiring point depends on your setup — for a service-layer deployment, inject it into the `EnginePort` adapter or the `PregelRuntime` constructor.

### Step 3 — Test

Test with lightweight stubs — no real Redis needed for unit tests. Mock the Redis client and verify `save` / `load` / `list_checkpoints` call the right methods.

---

## Example 2: Custom Guardrail Hook

**Goal**: Add a profanity filter that blocks LLM responses containing banned words.

### Step 1 — Implement

```python
from __future__ import annotations

from hecate.engine.guardrail import PostLLMHook


class ProfanityFilterHook(PostLLMHook):
    """Block LLM responses containing words from a banned-words list."""

    def __init__(self, banned_words: set[str]) -> None:
        self._banned = {w.lower() for w in banned_words}

    async def on_post_llm_call(
        self,
        response: str,
        context: dict,
    ) -> str:
        """Check the response; raise to block, or return modified text."""
        words_in_response = set(response.lower().split())
        if words_in_response & self._banned:
            raise ValueError(
                "Response blocked by ProfanityFilterHook: "
                f"matched {words_in_response & self._banned}"
            )
        return response  # pass through unchanged
```

### Step 2 — Wire

Register the hook in the agent's `guardrail_config`, or inject it directly into the worker's `SecurityHookSet`. See [Guardrails and Hooks](../concepts/guardrails.md#configuring-hooks).

### Step 3 — Test

```python
async def test_profanity_filter_blocks_banned_word():
    hook = ProfanityFilterHook(banned_words={"spam", "scam"})
    # Should pass
    await hook.on_post_llm_call("Hello world", {})
    # Should block
    try:
        await hook.on_post_llm_call("This is spam", {})
        assert False, "Should have raised"
    except ValueError:
        pass  # expected
```

---

## Example 3: Custom SchedulerStrategy

**Goal**: Prioritize nodes by a `priority` attribute instead of FIFO.

### Step 1 — Implement

```python
from __future__ import annotations

from hecate.engine.scheduler import SchedulerStrategy


class PriorityScheduler(SchedulerStrategy):
    """Select the highest-priority ready node first."""

    def select_next(
        self,
        ready_nodes: list[str],
        node_metadata: dict[str, dict],
    ) -> str | None:
        if not ready_nodes:
            return None
        return max(
            ready_nodes,
            key=lambda nid: node_metadata.get(nid, {}).get("priority", 0),
        )

    def set_weights(self, weights: dict[str, float]) -> None:
        """No-op for priority scheduling (weights not used)."""
        pass
```

### Step 2 — Wire

Pass the scheduler to the `PregelRuntime` constructor or the graph compiler configuration.

---

## Testing patterns

The engine is designed for testability with lightweight stubs — no mocking frameworks required:

| Pattern | When to use | Example |
|---------|-------------|---------|
| **InMemory defaults** | Unit testing engine logic | `InMemoryCheckpointStore`, `InMemoryContextEngine`, `NoOpPreLLMHook` |
| **Stub classes** | Testing a specific extension | `SimpleWorker`, `InterruptWorker` (see `tests/test_engine/`) |
| **Direct ABC test** | Verifying your implementation | Instantiate your class, call methods, assert results |

```python
# Verify your CheckpointStore is a valid implementation
from hecate.engine.checkpoint import CheckpointStore

assert isinstance(my_store, CheckpointStore)  # duck-type check
# Or test that the ABC cannot be instantiated directly:
import pytest
with pytest.raises(TypeError):
    CheckpointStore()  # abstract — cannot instantiate
```

---

## What not to do

- **Don't import from `services/` in your engine extension.** The engine must remain dependency-free. If your extension needs a service, wire it via the adapter layer, not inside the engine code.
- **Don't suppress exceptions in hooks.** A hook that silently swallows errors defeats the security model. If the operation should proceed, return normally; if it should be blocked, raise.
- **Don't forget the `from __future__ import annotations`** at the top of every file — it's required by the project's ruff configuration.
- **Don't skip type annotations.** All public methods require annotations (enforced by mypy with `strict=true`).

---

## Further reading

- [Extension Points Reference](../reference/extension-points.md) — all 11+4 interfaces with full method signatures
- [Guardrails and Hooks](../concepts/guardrails.md) — the four hook types and how they fit in the superstep loop
- [The Execution Engine](../concepts/engine.md) — where extensions plug into the Pregel runtime
- [ADR-016: Platform SPI Architecture](../design/adr/016-platform-spi-architecture.md) — why Hecate chose the SPI pattern
- [Glossary](../reference/glossary.md) — SPI, ABC, EnginePort, and other terms
