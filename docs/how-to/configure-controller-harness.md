# Configure frozen Controller context

The Controller Harness composes static, versioned context into the base input
of a controller run, and a per-turn pipeline guards, projects and shapes every
model decision the loop makes. The platform and Runner use the same section
parser, renderer, decision binding and turn pipeline. This slice supports
frozen context composition plus the per-turn semantic order — pre-call guard
rejection, context processor projection, provider shaping and output
governance. Platform Controller tasks can additionally enable the governed Skill
catalog described below. Dynamic Memory and planner/model-strategy configuration
are not part of the declaration.

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
handed to the runtime model port, alongside the full versioned input snapshot
(`harness_input`, snapshot version 1): the governed, projected, provider-shaped
messages, model configuration and visible tool definitions, deep-copied before
the port can mutate its arguments. The snapshot rides the accepted decision —
one transaction, covered by the record's content digest — and its own digest
must agree with `harness_input_sha256`, so a tampered or truncated snapshot
fails validation and the recovery path refuses the record instead of
re-deriving it. The boundary is the runtime model port, not a vendor's final
wire request; port-internal credentials never enter the snapshot, and the
snapshot never appears in governance events or observation surfaces.
Decisions committed before this binding (or by a responder without a
pipeline) carry the digest or nothing at all and keep recovering under their
original contract — history is never backfilled. Guard verdicts remain live;
freezing a projection policy does not override a known policy denial.

Only an absent snapshot field denotes a historical record. An explicit null
snapshot or a non-integer snapshot version is invalid. Independent-process
recovery and causal input-revision coverage live in
[`test_agent_controller_loop.py`](../../packages/hecate-durable/tests/test_agent_controller_loop.py);
[`test_accepted_decision_store.py`](../../packages/hecate-durable/tests/test_accepted_decision_store.py)
also verifies that an outbox failure rolls back the snapshot with its decision.

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

## Governed skill catalog (platform Controller opt-in)

Submit `"controller_loop": true` and `"controller": {"skill_catalog": true}`
to enable the platform capability. The flag must be a boolean. Existing tasks
and submissions without the flag retain their previous Skill behavior.
Caller-authored static `harness.skills` sections remain text snapshots and do
not grant access to resources in this catalog.

The first dispatch resolves the frozen AgentVersion's Skill names and reference
manifest through the existing provider, auto-load, discovery and budget rules.
It persists the rendered catalog and the renderer's structured selection before
calling the model. Each selected resource binds its exact identity, workspace,
rendered L2 bytes and complete content snapshot. Pinned resources additionally
bind the selected version and its full snapshot digest; resources without an
explicit version pin freeze the served live content, even if they have other
published versions. In progressive mode, only auto-load content appears in full
before an L2 request. The existing `SKILL_PROGRESSIVE_DISCLOSURE` escape hatch
still applies when first freezing a new task: disabling it renders full admitted
Skill blocks up front. That rendered choice is frozen too; changing the setting
does not rewrite an existing catalog.

`load_skill` uses the same task-bound loader through the actual builtin registry,
including on Agents with no business tools. Budget-clipped and unadvertised
names cannot be loaded; subsequent discovery, same-name provider overrides,
live edits and newer versions cannot expand or rewrite the task's surface.
Skill descriptions are never parsed to derive access rights. Missing or
cross-workspace pins, mismatched hashes and malformed references refuse initial
freezing without a model call; the opt-in path never falls back to live content.

Every accepted decision binds the catalog's version and identity/content digest,
alongside its existing Harness and actual model-input snapshot. Later dispatches
reuse the committed catalog; an accepted chain with missing, soft-deleted,
corrupt or differently owned catalog state stops for reconciliation. It does not
rebuild the catalog from current data. Historical decisions and completed
`load_skill` actions replay through their existing identities and ledger results,
without another model call or content load.

Freezing content does not freeze permission. Before each fresh model call and L2
load, the platform checks the exact resource's current governance: deletion,
`model_invocable`, plugin status and scope, and the applicable discovery policy
and trust floor. Withdrawal of automatic exposure also refuses fresh exposure.
A live denial stops execution; it does not silently replace the frozen catalog.
Pinned version deletion or content corruption likewise refuses a fresh load.
Stored historical facts remain intact, and replayed output retains the existing
output-governance path.

Skill `allowed_tools`, `scripts` and `references` do not install tools, execute
scripts or widen permissions. Cancellation, deadlines, turn budgets, approval
and input-wake rules remain in force. The Runner keeps its static Harness;
dynamic Memory, script execution and a Runner Skill backend remain outside this
platform capability.

Deploy the Alembic schema upgrade before enabling the flag. Catalog rows are
recovery state: downgrade refuses while any remain, including soft-deleted rows.
Do not delete catalog rows to retry a task under a different Skill definition;
submit a new task with the intended version instead.

Validation uses deterministic model ports with actual Skill rows, rendering,
registry/builtin loading, durable decisions and action results. A separate
process verifies frozen builtin loading. These tests do not certify real-model
quality, PostgreSQL concurrency or the complete cross-process failure matrix.

## No-progress protection (opt-in)

A controller task can declare how many consecutive identical observation turns it tolerates:

```json
{"controller": {"max_turns": 8, "no_progress": {"limit": 3, "observations": ["web_search"]}}}
```

- **Explicit only.** Without the declaration nothing changes. Unknown declaration fields are rejected. Observation tools must have a confirmed read-only role before the first model call. The platform uses the kernel classification; the Runner also attests its native `query_inventory` adapter and admitted manifest read tools, pinning that host scope in the decision references. This does not change action authorization or retry classification.
- **Facts, not appearances.** A turn's observation is the ordered set of recorded facts — tool, canonical arguments, and the ledger's canonical result digest (the same value recovery resolves). Tool-name repetition, model text and Todo state never trigger a stop; a changed result resets the window.
- **Change and waits.** Keep legitimate polling tools outside `observations`; they retain the normal turn, deadline and budget bounds. Any succeeded action outside the observation set makes the current window inapplicable and resets it, without claiming that the business goal progressed. Approval waits and explicit input-request outcomes are control facts, never observations, including after a wake acknowledges the request. A consumed human-input revision opens a fresh window without resetting the turn budget, deadline or policy.
- **Missing facts.** An observed action without a verifiable ledger result digest requires reconciliation; missing evidence never resets the window as if progress occurred. The complete, identity-bound action report is validated before the stop is classified.
- **Binding and recovery.** Every decision pins the policy's version and digest; a policy rewritten under the same version conflicts on replay. Re-entry rebuilds the streak from the committed decisions and ledger facts and stops at the same place — no parallel progress ledger.
- **The stop is honest.** Reaching the limit stops the loop before the next model call with the stop reason `no_progress_limit` and category `controller_no_progress_limit`. Both hosts persist a `no_progress` summary with the policy version/digest, limit, consecutive count, turn boundary and last decision reference. Completed results stay queryable, the task is never marked successful, and nothing re-queues or re-runs. Cancellation, deadlines, authorization denials and unknown side effects keep their original classifications and precedence.

Independent-process stop recovery is covered by
[`test_agent_controller_loop.py`](../../packages/hecate-durable/tests/test_agent_controller_loop.py).
This is scoped repeated-observation protection, not certification that arbitrary
business stagnation or semantic cycles can be detected.
