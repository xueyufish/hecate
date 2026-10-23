---
name: hecate-parallel-plan
description: Plan complete, independently verifiable Hecate work packages across concurrent Git worktrees. Use when choosing another parallel task, avoiding conflicts with ongoing work, or preparing an implementation handoff for GLM or another developer. Produces a grounded plan, not implementation or an OpenSpec change.
---

# Hecate parallel work planning

Optimize total delivery, handoff, review, and integration cost. Recommend a coherent
outcome with implementation, tests, and necessary documentation together. Neither
minimum diff nor maximum branch size is the objective. Keep each PR focused on one
purpose; a work package may contain several related tasks and logical commits.

## Establish the goal and live state

Use the user's full goal and known ongoing assignments. If the goal is absent,
inspect the repository and available backlog first, then ask for the material
priority that cannot be inferred. Distinguish a candidate inferred from backlog
from a user-selected requirement. Do not invent work merely to fill a worker slot.

Read the current repository's `AGENTS.md`. Refresh `origin/main` with
`git fetch origin` when assessing the latest mainline, unless the user requests
offline work. Fetch failure or offline mode means freshness is unverified: report
that limitation and use the available SHA without describing it as latest.

From the repository root, run the bundled inventory helper with the local Python
environment (on Windows use `.venv/Scripts/python.exe`):

```bash
python .agents/skills/hecate-parallel-plan/scripts/inventory_worktrees.py --repo .
```

For each user-specified worktree directory, add `--root "absolute-directory"`.
The option is repeatable. Always enumerate all registered worktrees, including
those outside that directory. Resolve paths from Git and the user's request;
do not hardcode a machine path or silently replace a missing directory with a
similarly named one. The helper reads local Git metadata only; it never fetches,
checks out branches, or changes files. Nonzero exit still emits JSON describing
partial results and errors. Diagnose those errors before making recommendations.

The snapshot includes mainline SHA, branch/HEAD, merge base, divergence, recent
commits, committed file changes, staged/unstaged/untracked paths, and worktree
flags. Merge base is an integration reference, not proof of the original branch
creation point. Separate clones found under requested roots are reported but
are not compared as if they shared this repository's refs.

Inspect relevant diffs, tests, callers, and architecture at the recorded revisions.
Use file lists to narrow reads; avoid dumping entire repositories or untracked
secrets. Read applicable nested `AGENTS.md` before designing work in that area.
In Hecate, account for `packages/hecate-runtime` versus forwarding shims under
`src/hecate/runtime`, domain boundaries, `execution/`, and `contracts/` consumers.

Git reveals completed edits, not future intent. Combine observed changes with
available task descriptions and user-provided GLM plans. Existing OpenSpec
artifacts can be read as evidence, but their presence does not activate OpenSpec.
Mark unknown future scope explicitly. A clean or old worktree is not evidence
that its owner has finished. Do not message other chats or developers merely
because this skill is being used.

## Choose package boundaries

Compare plausible complete outcomes against ongoing work. Include implementation,
tests, required configuration/docs, and observable acceptance criteria in each
candidate. Stop expanding when another independent purpose or substantial new
shared dependency would be introduced. Prefer a concise recommendation over an
exhaustive decomposition of the whole repository.

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
ask only when the missing scope materially changes the recommendation.

## Deliver a ready-to-use handoff

Respond in the user's language. Include a compact inventory/evidence summary,
candidate conflict assessment, and one recommended package (or a serial outcome).
Then provide a self-contained task message the user can paste to GLM containing:

- **Outcome and acceptance:** observable behavior and meaningful tests.
- **Work location:** suggested worktree name/path, available branch name, base
  ref and inspected SHA. Check existing names before recommending them. Follow
  repository branch conventions. Proposals are not created worktrees.
- **Scope:** owned implementation/tests/docs and relevant entry points; shared
  owners, agreed interfaces, dependencies, and excluded independent work.
- **Autonomy:** complete related implementation, tests, and routine fixes without
  per-step approval. Pause for material requirement ambiguity, incompatible
  shared-interface changes, an actual ownership collision, or missing authority.
- **Verification:** concrete commands using existing test paths and shared
  checks from this checkout. Separate checks actually run during planning from
  commands the implementer still needs to run; do not invent passing results.
- **Integration and return:** dependency/landing order, re-inventory before
  integration, scoped cross-boundary tests after integration, and a return report
  with branch/base/final commit or dirty status, changed paths, observed test
  results, and unresolved risks. Report pending CI/review separately.

Make planning facts sufficiently precise for a later review: include inspected
SHAs and uncertain assignments. The snapshot is not an ownership lock; concurrent
work may change it. Do not introduce a mandatory registry, per-task proposal,
or repeated phase confirmations. If the user requests a saved handoff, save only
the requested artifact in an authorized topic checkout; otherwise return it in
the conversation.

Planning alone does not authorize implementation, worktree creation, commit,
push, merge, or messaging GLM. If the user also requests execution, proceed within
that authorization and repository rules. Preserve explicit push confirmation.
Default to ordinary development; invoke OpenSpec only through the user's manual
OpenSpec command or skill selection. This skill never runs propose/apply/archive
as part of planning.
