---
name: hecate-parallel-plan
description: Recommend the next independently verifiable Hecate work package, plan an already selected task, or do both. Use when checking a task against concurrent Git worktrees or preparing a handoff for an implementer.
---

# Hecate parallel work planning

Optimize total delivery, handoff, review, and integration cost. Recommend a coherent
outcome with implementation, tests, and necessary documentation together. Neither
minimum diff nor maximum branch size is the objective. Keep each PR focused on one
purpose; a work package may contain several related tasks and logical commits.

## Choose the planning mode

- **Recommend only:** When asked to pick the next task, inspect the repository,
  available backlog, and ongoing work. Recommend one work package with reasons,
  potential overlap, dependencies, and brief acceptance criteria.
- **Plan a selected task:** When the user supplies a concrete outcome or selects
  a work package from an earlier recommendation, plan that scope directly.
- **Recommend and plan:** When asked for both, or invoked without a task or
  a narrower request, recommend one package and include its implementation
  plan in the same response. Distinguish the recommendation from a
  user-selected requirement.

Ask about priority or scope only when the available evidence leaves a material
decision unresolved. Do not invent work to fill a worker slot.

## Establish the live state

Read the current repository's `AGENTS.md`. Refresh `origin/main` with
`git fetch origin` when assessing the latest mainline, unless the user requests
offline work. Fetch failure or offline mode means freshness is unverified: report
that limitation and use the available SHA without describing it as latest.

Check concurrent work when the user asks for a comparison or available evidence
suggests a change or dependency that could affect the package. For that check,
run the bundled inventory helper from the repository root with the local Python
environment (on Windows use `.venv/Scripts/python.exe`):

```bash
python .agents/skills/hecate-parallel-plan/scripts/inventory_worktrees.py --repo .
```

Add `--root "absolute-directory"` for each worktree directory the user specifies.
Resolve paths from Git and the user's request. The helper reads local Git
metadata and includes all registered worktrees. Use its output to locate
user-specified targets and screen for relevant paths, contracts, and integration
dependencies. Inspect and report worktrees only when the user included them
in scope or the evidence connects them to a candidate. Registration, branch
names, and unknown future plans alone do not establish relevance. Report
inventory errors that limit a conclusion about the task. Separate clones do
not share this repository's refs. A merge base serves as a comparison point.

Inspect relevant diffs, tests, callers, and architecture at the recorded revisions.
Use file lists to narrow reads; avoid dumping entire repositories or untracked
secrets. Read applicable nested `AGENTS.md` before designing work in that area.
In Hecate, account for `packages/hecate-runtime` versus forwarding shims under
`src/hecate/runtime`, domain boundaries, `execution/`, and `contracts/` consumers.

Git reveals completed edits, not future intent. Combine observed changes with
available task descriptions and plans from implementers. Existing OpenSpec
artifacts can be read as evidence, but their presence does not activate OpenSpec.
Call out unknown work only when it could materially change the proposed scope
or landing order. Do not message other chats or developers merely because this
skill is being used.

## Apply the evolution plan

For roadmap recommendations, start with the
[global task tracking table](../../../docs/refactor/enterprise-agent-platform-evolution-plan.md#全局任务跟踪)
at the inspected revision. Follow the user's stated priority when given.
Otherwise scan in table order for unchecked rows with workable remaining scope.
Skip active work already covered by an implementer and work whose prerequisites
or triggers are still unmet. Use partial progress and linked evidence to identify
the remaining scope. Verify uncertain status against Git and relevant worktrees
before recommending it.

Read the candidate's task section and only the milestone, acceptance scenarios,
and prerequisite sections needed to confirm the scope. Verify prerequisites on
the proposed base. For a prerequisite that delivers a specific interface or
capability, check that boundary. Record the interface and landing order if a
dependent package can start before it lands. Verify the triggering evidence for
conditional work. Include the task ID and exit criteria in the recommendation
or handoff.

For an explicitly selected roadmap task, go directly to its row and task section.
If the inspected revision has no table, use its task sections and available
status evidence. For work outside the evolution plan, use the relevant backlog
and repository evidence.

## Choose package boundaries

Compare plausible complete outcomes against ongoing work. Include implementation,
tests, required configuration/docs, and observable acceptance criteria in each
candidate. Stop expanding when another independent purpose or substantial new
shared dependency would be introduced. Prefer a concise recommendation over an
exhaustive decomposition of the whole repository.

Use roadmap tasks as scope and dependency references. Split a broad task by
independently verifiable outcomes when it spans separate PR purposes. For
retirements, check callers, entry points, and data or migrations still used by
retained capabilities. Assign shared boundaries to a named package and specify
what dependents can use once it lands. Keep each package runnable on its proposed
base and state which milestone criteria remain for later packages.

Classify the evidence, rather than promising zero conflicts:

- **Text overlap:** shared paths or nearby edits needing inspection. Same-file
  edits are not automatically incompatible. Report confirmed incompatible edits
  separately from potential overlap.
- **Semantic dependency:** different files sharing behavior, public contracts,
  events, schemas, migration ordering, tenant/security rules, runtime semantics,
  configuration, or acceptance assumptions.
- **Independent within known scope:** no identified implementation or acceptance
  dependency after examining relevant consumers. State remaining uncertainty.

Assign one owner for each shared change that actually matters. Hecate hotspots
include `contracts/`, public models/schemas, migrations, `pyproject.toml` and
`uv.lock`, composition wiring under `core/`, common fixtures, and CI/release
configuration. Do not reserve entire directories unnecessarily. For dependent
packages, establish the concrete interface first, then permit parallel work
against it with an explicit integration order. Prefer landing the prerequisite
before dependent rebases; if a stacked branch is useful, name its exact base and
explain the dependency. Do not rebase somebody else's worktree during planning.

When no useful complete package is sufficiently independent, recommend serial
delivery and explain the shared boundary. Do not shrink the assignment to a
trivial fragment solely to claim parallelism. Unknown plans limit confidence;
ask only when a plausible dependency leaves a material decision unresolved.

## Deliver the selected output

Respond in the user's language. Summarize the relevant inventory and evidence,
including conflicts, dependencies, and any recommended serial order.

For a recommendation-only request, give the recommended package, why it is
worth doing now, and its brief acceptance criteria. End there.

For planning, provide a self-contained handoff for the implementer. Keep it
usable in a chat, task tracker, or automated workflow. Include:

- **Outcome and acceptance:** observable behavior and meaningful tests.
- **Work location:** suggested worktree name/path, available branch name, base
  ref and inspected SHA. Check existing names before recommending them. Follow
  repository branch conventions. Proposals are not created worktrees.
- **Scope:** owned implementation/tests/docs and relevant entry points; shared
  owners, agreed interfaces, dependencies, and excluded independent work.
- **Implementation steps:** the sequence of changes and relevant entry points.
- **Autonomy:** complete related implementation, tests, and routine fixes without
  per-step approval. Pause for material requirement ambiguity, incompatible
  shared-interface changes, an actual ownership collision, or missing authority.
- **Verification:** concrete commands using existing test paths and shared
  checks from this checkout. Separate checks actually run during planning from
  commands the implementer still needs to run. Identify any acceptance evidence
  that requires real PostgreSQL, object storage, a model, network isolation, or
  client interoperability. Record the environment and distinguish simulated,
  skipped, and unverified cases from observed acceptance results.
- **Integration and return:** dependency/landing order, recheck relevant
  concurrent changes before integration, scoped cross-boundary tests after
  integration, and a report with branch, base, final commit or dirty status,
  changed paths, observed test results, and unresolved risks. Report pending CI/review separately.

Make planning facts sufficiently precise for a later review: include inspected
SHAs and uncertain assignments. The snapshot is not an ownership lock; concurrent
work may change it. Do not introduce a mandatory registry, per-task proposal,
or repeated phase confirmations. If the user requests a saved handoff, save only
the requested artifact in an authorized topic checkout; otherwise return it in
the conversation.

Planning alone does not authorize implementation, worktree creation, commit,
push, merge, or contacting the implementer. If the user also requests execution,
proceed within that authorization and repository rules. Preserve explicit push
confirmation.
Default to ordinary development; invoke OpenSpec only through the user's manual
OpenSpec command or skill selection. This skill never runs propose/apply/archive
as part of planning.
