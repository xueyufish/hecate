# How to Ship a Third-Party Memory Backend

Hecate's chat path (`RuntimePort.knowledge_query`) and the MCP
`knowledge_search` tool both delegate retrieval to a single in-process
**memory provider**. The default is `builtin` — the in-process RAG backend
shipped in the `hecate-memory` package. Any other installed Python package
that registers itself under the `hecate.memory_providers` entry-point group
can take over retrieval without forking the core.

This guide walks through writing a `hecate-memory-<vendor>` package that
plugs in cleanly. The contract is duck-typed — no ABC, no base class — so the
integration is a single Python module plus a `pyproject.toml` snippet.

---

## The five steps

### Step 1 — Pick the third-party SDK

Choose the vendor you want to integrate — e.g. `mem0`, `zep`, `letta`, or
your own service. Make sure the SDK exposes an async search interface that
maps to Hecate's expected shape.

### Step 2 — Create the adapter package

Scaffold a package named `hecate-memory-<vendor>` (the `<surface>-<vendor>`
convention — see [../README.md](../README.md)). It depends on Hecate for the
Protocol types it conforms to:

```toml
# pyproject.toml
[project]
name = "hecate-memory-<vendor>"
version = "0.1.0"
dependencies = [
    "hecate>=0.1.0",
    "<vendor-sdk>>=1.0",
]

[project.entry-points."hecate.memory_providers"]
<vendor> = "hecate_memory_<vendor>.provider:provider"
```

The entry-point **name** (the key on the left) is the value users set
`HECATE_MEMORY_PROVIDER` to. Pick something short and recognizable
(`mem0`, `zep`, `letta`, …).

### Step 3 — Implement the factory

A minimal adapter module looks like this:

```python
"""<vendor> memory backend for Hecate."""

from __future__ import annotations

from hecate.ops.orchestration.memory_provider import MemoryProvider


class VendorMemoryProvider:
    """Adapter implementing Hecate's MemoryProvider contract via <vendor>'s SDK."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int = 10,
        mode: str = "hybrid",
        workspace_id: str | None = None,
    ) -> list[Any]:
        # `mode` is "hybrid" / "dense" / "sparse"; map to whatever your SDK supports.
        # `workspace_id` is Hecate's tenant scope — pass it through for isolation.
        raw = await self._client.search(
            collection=collection_name,
            q=query,
            top_k=limit,
            workspace=workspace_id,
        )
        return [
            _Hit(content=h.text, score=h.score, metadata=h.metadata)
            for h in raw
        ]


@dataclass
class _Hit:
    content: str
    score: float
    metadata: dict[str, Any]


def provider() -> MemoryProvider:
    """Zero-arg factory — reads env vars / config and returns the backend instance.

    Return ``None`` when the integration is not configured (missing API key,
    disabled by feature flag, …); Hecate then falls back to ``builtin``.
    """
    api_key = os.environ.get("VENDOR_API_KEY")
    if not api_key:
        return None
    client = VendorClient(api_key=api_key)
    return VendorMemoryProvider(client=client)
```

The contract — what Hecate actually calls — is:

| Method | Required? | Notes |
|---|---|---|
| `async def search(collection_name, query, *, limit=10, mode="hybrid", workspace_id=None) -> list[SearchHitLike]` | yes | Core calls this from `AgentExecutionPort.knowledge_query` (chat path) and the MCP `knowledge_search` tool. |
| `SearchHitLike` (Protocol) | yes | `content: str` / `score: float` / `metadata: dict`. Return any object with those attributes — a dataclass, a Pydantic model, a plain class. |

**What the contract does NOT cover** (intentionally):

- **Document ingestion / collection creation**. Hecate's MCP `knowledge_create`
  and `knowledge_ingest` tools continue to use the `builtin` backend for the
  writing side. If your SDK stores content elsewhere, ingest into your
  system out-of-band — Hecate won't do it for you.
- **Auth / authorization**. Use the same `workspace_id` you received from
  Hecate; do not invent your own multi-tenant scheme.
- **Result post-processing / reranking**. Return raw hits; Hecate ranks by
  `score` and limits to the top 20 across all knowledge bases.

### Step 4 — Install and select

```bash
pip install hecate-memory-<vendor>
export HECATE_MEMORY_PROVIDER=<vendor>
# restart the Hecate process
```

That's it — `chat` calls now go through your backend. `knowledge_search`
MCP tool calls do too.

### Step 5 — Verify

Smoke-test the wiring:

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "What did we ship last week?", "knowledge_base_ids": ["..."]}'
```

If the response includes citations from your vendor's storage, the
integration is live. To fall back, unset `HECATE_MEMORY_PROVIDER` (or set it
to `builtin`) and restart — Hecate picks up the change on the next process
boot.

### Roll back

Uninstalling the package is the rollback:

```bash
pip uninstall hecate-memory-<vendor>
export HECATE_MEMORY_PROVIDER=builtin   # or unset
# restart
```

Hecate logs a warning at startup if `HECATE_MEMORY_PROVIDER` is set to a name
with no entry point, then degrades to empty results — no crash, no missing
import.

---

## Worked example: `hecate-memory-mem0`

A complete reference implementation lives at
`packages/hecate-memory-mem0/` (placeholder — actual package not yet
shipped). The shape follows this guide; refer to it when writing your own.

## What you should NOT do

- **Do not subclass `RuntimePort`.** This contract only replaces the
  knowledge-search capability, not the whole agent-execution port. Other
  surfaces have their own entry-point groups (`hecate.llm_adapters`,
  `hecate.ops_backends`, … — see [../README.md](../README.md) once they
  land).
- **Do not raise from `provider()`.** Hecate catches exceptions from
  factories and falls back to the default; raising complicates startup logs
  and breaks the "degrade to no-op" contract.
- **Do not import `hecate_memory.rag.*` from your adapter.** Your package
  replaces `hecate-memory`'s search backend — depending on it would create a
  circular deployment (uninstalling `hecate-memory` would break your
  backend). Use only the public types from
  `hecate.ops.orchestration.memory_provider`.
- **Do not skip the zero-arg factory signature.** The host invokes
  `ep.load()()` — no arguments — at the discovery stage.

## Reference

- `src/hecate/core/composition/memory_provider.py` — the resolver
  and Protocols.
- `packages/hecate-memory/src/hecate_memory/provider.py` — the `builtin`
  reference implementation.
- `tests/test_services/test_orchestration/test_memory_provider.py` —
  contract tests showing how to monkeypatch the resolver for your own
  integration tests.