# Configure frozen Controller context

The Controller Harness composes static, versioned context into the base input
of a controller run, and a per-turn pipeline guards, projects and shapes every
model decision the loop makes. The platform and Runner use the same section
parser, renderer, decision binding and turn pipeline. This slice supports
frozen context composition plus the per-turn semantic order — pre-call guard
rejection, context processor projection, provider shaping and output
governance; dynamic Skill/Memory loading and planner/model-strategy
configuration are not part of the declaration.

## Platform tasks

Include a `harness` block in the task submission's `input`, alongside the
explicit `controller_loop` binding:

```json
{
  "controller_loop": true,
  "messages": [{"role": "user", "content": "Prepare a status report."}],
  "harness": {
    "version": 1,
    "sections": [
      {"type": "instructions", "content": "Cite the supplied evidence."},
      {"type": "skills", "content": [{"name": "report", "revision": "approved"}]},
      {"type": "memory", "content": {"report_language": "English"}}
    ]
  }
}
```

Caller sections retain their declared order. The platform appends the persona
from the frozen AgentVersion configuration, the task's output-schema instruction
when present, and the original task messages. Callers cannot declare `persona`,
`output_schema` or `task` sections on this entry.

New platform controller submissions with no `harness` block receive a default
empty declaration. Tasks persisted before this binding retain their original
manual message composition. A non-controller submission rejects a declaration.

## Runner tasks

Enable `controller_loop` in the Runner profile, with its required durable store.
Include the same `harness` block in the run input beside `prompt`. Runner callers
may also declare a nonblank `persona` section because this host has no platform
AgentVersion persona to append. The Runner appends any declared output-schema
instruction, followed by its prompt or fallback task input. A run without a
declaration retains the previous base-input path and skips the new turn pipeline.
Use an empty `harness` object to enable the pipeline without extra sections.

`instructions` and `persona` require nonblank strings. `skills` and `memory`
accept JSON snapshots; these fields do not load a Skill or query a Memory
provider. They render as labelled JSON system messages. Supply content that is
appropriate for that instruction context. Section names do not install tools,
grant permissions or change action authorization. Unknown section types and
unknown declaration fields are rejected at admission.

## Recovery and compatibility

Each decision binds `harness_version` and `harness_digest`, including the ordered
section contents and host-derived sections. The base messages must match the
frozen composition. Equivalent schema objects render with stable key ordering.
Composition detaches caller-owned nested values; direct mutation of a composed
spec is detected before rendering or serialization.

Re-entering a run restores committed decisions and completed actions through
the existing durable stores. Changing the declaration under the same version
conflicts on replay and requires reconciliation. Increasing the version also
changes the binding; submit a new task for a new composition. Human input
revisions retain their own causal turn boundaries and do not replace the base
context. The Harness does not change cancellation, deadlines, budgets or
delivery-acceptance rules.

Pipelined decisions also bind the `harness_pipeline` version, resolved processor
parameters, model and context budget. A changed projection policy conflicts on
replay even when the base messages are unchanged. The accepted output stores
`harness_input_sha256`, a digest of the projected messages and configuration
handed to the runtime model port. This binds the invocation without retaining
a separate full input snapshot. Guard verdicts remain live; freezing a
projection policy does not override a known policy denial.

## Per-turn pipeline

For tasks with a Harness binding, each fresh model decision passes the shared
turn pipeline:

1. **Pre-call guard** (platform): the workspace guardrail bundle's
   `AGENT_REQUEST` chain runs before the model is invoked. BLOCK refuses the
   turn — no model usage, no committed decision — and the loop stops with the
   classified reason `model_call_rejected`. This applies with or without tools.
   SANITIZE replaces the messages and any supplied tool filter the model sees.
2. **Context projection** (both hosts): the context processor chain projects
   the conversation within its token budget — non-destructively, as a
   per-invocation view. The platform resolves the agent version's frozen
   `context_processors` policy; the Runner uses the platform-default policy.
   Oversized tool results are capped. If projection cannot fit the budget,
   `token_capped` stops the loop before another model call. This stop creates
   no final-answer decision and cannot mark the task successful.
3. **Provider shaping** (both hosts): the runtime port's optional
   `context_assemble` capability shapes messages and tools for the provider
   before the call.
4. **Output governance** (platform): the `LLM_RESPONSE` chain inspects the
   completed response. BLOCK refuses it as an accepted output (classified
   `model_call_rejected`); SANITIZE replaces the accepted text and tool calls.
   Removed calls stay removed. Malformed governed output fails closed, and
   modified arguments still pass the trusted action boundary.

This Controller profile supports transient projection compression. Explicit
offload policies and `surface_replacement` compression are rejected before any
model call because this pipeline does not provide their storage and recovery
substrate. The default ladder's unavailable offloader performs no work; its
projection fallback and termination remain available.

Committed decisions replay from storage without model calls, so the pipeline
runs only for fresh turns and never re-projects committed history. The
projection never deletes recovery facts: the frozen composition digest, input
revisions and the acceptance binding stay the authority.

The Runner configures no LLM guard chains today (its governance lives on the
tool side), so stages 1 and 4 are pass-throughs on that host until it declares
guard hooks. This slice does not certify model quality or production
profiles, and the declared sections remain static snapshots: they do not load
a Skill provider, query a Memory backend, or configure a planner.
