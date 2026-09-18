# HTTP NLI Endpoint Contract (1.3.5e Stage 2)

The `http_nli` grounding-scoring backend POSTs `(claim, evidence)` pairs to
an operator-configured endpoint. This lets a deployment run its own
entailment model (MiniCheck-class, or any service exposing the shape below)
without the platform embedding a model runtime.

## Request

`POST <nli_endpoint>` with a JSON body:

```json
{
  "pairs": [
    {
      "claim": "The platform was founded in 2023.",
      "evidence": "chunk text 1\n---\nchunk text 2"
    }
  ]
}
```

- Multiple evidence texts for one claim are joined with `\n---\n`.
- A pair with no evidence is sent with an empty `evidence` string (only
  happens when scoring is enabled without citation provenance and without
  fallback retrieval — normally such claims are never sent).

## Response

`200 OK` with a `results` array aligned by index with `pairs`. Each item is
one of two shapes:

Three-way (preferred):

```json
{
  "results": [
    { "verdict": "supported", "confidence": 0.93 },
    { "verdict": "contradicted", "confidence": 0.81 },
    { "verdict": "unverifiable", "confidence": 0.4 }
  ]
}
```

Binary support (accepted):

```json
{ "results": [{ "support": true }, { "support": false }] }
```

Mapping rules (enforced platform-side, `HttpNliScorer`):

- `verdict` must be `supported` / `contradicted` / `unverifiable`, with
  `confidence` in `[0, 1]`; anything else degrades that pair.
- Binary `support: true` maps to `supported` with confidence `0.8` unless
  the item carries a `confidence`; `support: false` maps to
  `unverifiable`. **A binary endpoint can never produce `contradicted`** —
  contradiction requires explicit disagreement semantics the binary shape
  does not carry.
- Missing/short `results` entries degrade their pair (recorded in the
  event as `degraded: true`, never surfaced to the caller).

## Operational semantics

- Timeout: `nli_timeout_ms` per pair (policy), applied as the request
  timeout over the batch, clamped to `[1s, 15s]`.
- Any transport or mapping failure degrades all pairs of the batch —
  scoring never fails the agent invocation.
- The endpoint is a deployment-local asset: no health checking, auth
  injection, or retries beyond the single request are performed by the
  platform. Put auth/retry concerns in a gateway in front of the model.
