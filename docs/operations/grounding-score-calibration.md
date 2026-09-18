# Grounding Score Calibration Runbook (1.3.5e Stage 2)

How to produce the false-positive / false-negative measurements that the
Stage 3 disposition-layer proposal requires. The Stage 2 scoring layer is
observational: every response-level verdict lands in the event log as a
`GROUNDING_SCORE` payload (a `CUSTOM` event with `event_name`), including a
`would_block` shadow disposition that has **no behavioral effect**.

## Inputs

- Gray-release nodes with `grounding_scoring.enabled=true` (default
  trigger `on_uncited` keeps cost proportional to citation misses).
- Human annotation capacity, via the existing annotation queue (7.4 trace
  backflow): annotate sampled sessions by inspecting the assistant message
  plus the cited chunks in the replay view.

## Step 1 — Sample `would_block` hits (false-positive candidates)

Query the EventStore for scored responses whose shadow disposition fired:

```
event_type = CUSTOM
payload.event_name = "GROUNDING_SCORE"
payload.would_block.triggered = true
```

For each hit, the annotation task is: *knowing what the chunks actually
said, would delivering this response have been wrong?*

- verdict **justified** (the response did assert something the evidence
  contradicted, or asserted without any retrievable support beyond the
  policy's tolerance) → true positive
- verdict **unjustified** (the claim was correct; the scorer mis-read the
  evidence, or the claim did not need chunk-level support) → false
  positive

False-positive rate = unjustified / annotated. This is the number that
decides whether automatic blocking is safe to ship.

## Step 2 — Reverse-sample non-hits (false negatives)

Query scored responses with `payload.would_block.triggered = false` and
annotate a random sample the same way, looking for claims the shadow
disposition *missed* (contradicted claims scored as supported, or
fabrications that scored unverifiable but below threshold). This gives the
miss rate at the current thresholds.

## Step 3 — Sweep thresholds (offline, no re-annotation needed)

`per_claim` verdicts + confidences are in every event, so threshold
alternatives can be evaluated offline against the same annotations: for
each candidate `shadow_thresholds` setting, recompute `would_block` over
the annotated corpus and re-derive the two rates. Pick the operating point
where the false-positive rate is acceptable for the deployment's trust
requirements; record it in the Stage 3 proposal.

## Step 4 — Close the loop

The Stage 3 disposition proposal must carry: sample sizes, both rates per
candidate threshold, and the chosen operating point. That closes the open
question left in the Stage 2 design ("Stage 3 disposition thresholds and
`would_block` default calibration").

## Sampling-size guidance

The runbook does not prescribe a fixed sample size — the required volume
depends on the deployment's tolerance. As a working floor: annotation
results below a few dozen hits per candidate threshold cannot distinguish
threshold settings from noise; scale the sample until adjacent threshold
candidates produce clearly separated rates.
