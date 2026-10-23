---
name: hecate-glm-review
description: Review a completed GLM implementation in an existing Hecate worktree against its stated goal and base revision. Use when GLM hands a branch back to Codex for verification, code review, regression analysis, and integration readiness. Produces findings and evidence; it does not modify, commit, push, or invoke OpenSpec by itself.
license: MIT
---

# Hecate GLM implementation review

Review the implementation in the worktree where GLM finished the task. Treat the
GLM handoff as evidence to verify, not as proof that the implementation is correct.
The default result is a read-only review that gives the user a clear decision about
whether the branch is ready for integration.

## Establish the review target

Use the worktree path, branch, base revision, task description, and GLM report from
the user. If a path is missing, inspect registered worktrees with:

```bash
git worktree list --porcelain
```

Choose a target only when its branch and task can be identified. Never silently
review a different checkout. A path under a requested worktree directory that is
not registered by Git is evidence to report, not a branch to guess.

Read the repository and applicable nested `AGENTS.md` files from the target. Check
the target's branch, HEAD, worktree state, and remote refs. Fetch `origin` when the
review asks for the latest mainline and network access is available; if fetching
fails or the user requests offline review, report the exact freshness limitation.
Do not assume that a clean worktree means the task is complete.

Resolve the comparison base in this order:

1. An explicit base SHA or ref from the GLM handoff.
2. The target's recorded merge base with `origin/main`.
3. A user-provided implementation start commit or tag.

Record the selected base and HEAD. If the branch was already squash-merged into
`origin/main`, `origin/main...HEAD` may hide the implementation; require the GLM
handoff's pre-implementation base or a specific commit range before claiming a
complete review. Do not infer a range from commit count or branch name.

Capture all review material without changing the checkout:

```bash
git status --short
git diff --name-status <base>...HEAD
git diff <base>...HEAD
git diff --name-status HEAD
git diff HEAD
git ls-files --others --exclude-standard
```

Include staged, unstaged, and untracked files in the review. An untracked file is
part of the handoff only when it belongs to the stated task; otherwise report it as
unexplained worktree state. Do not read secrets or dump unrelated repository files.

The review must not run `git checkout`, `reset`, `clean`, `stash`, `rebase`, merge,
or any command that discards or relocates the GLM work. Do not edit files, create a
worktree, send messages, commit, push, or start a pull request during review.

## Review the complete change

Start from the requested outcome and acceptance criteria. Trace changed code to
its callers, adapters, schemas, persistence, error paths, and tests. Inspect the
smallest surrounding context needed to validate behavior; do not reward a narrow
diff when the feature boundary is incomplete.

Check these areas when relevant:

- Functional behavior, edge cases, failure handling, and backward compatibility.
- Authorization, tenant isolation, data exposure, and trust-boundary assumptions.
- Public APIs, events, contracts, schemas, serialization, and migration ordering.
- Async behavior, transactions, retries, cancellation, concurrency, and resource cleanup.
- Hecate runtime semantics, `packages/hecate-runtime`, forwarding shims under
  `src/hecate/runtime`, `execution/`, composition wiring, and domain boundaries.
- Tests that prove the acceptance criteria, including negative and regression paths.
- Configuration, dependency declarations, CI/release gates, documentation, and
  generated artifacts when the change touches them.

Classify findings by impact:

- **P0:** unsafe to integrate or likely to cause severe data, security, or service failure.
- **P1:** correctness or compatibility defect that should block integration.
- **P2:** real defect or missing coverage that should be fixed, but does not block every integration path.
- **P3:** minor maintainability or clarity issue; report only when it is actionable.

Report each finding with the exact file and line, observed evidence, impact, and a
concrete remediation direction. Do not report style preferences as defects when
the repository tools already enforce the convention. Separate confirmed findings
from questions and residual risk. If the task is underspecified, identify the
missing acceptance decision instead of inventing one.

## Verify the claims

Use the GLM report to select focused checks, then reproduce the checks that matter
for the changed paths. Prefer the repository's shared commands and the target's
Python environment:

```bash
python scripts/dev.py test <affected tests> -q
python scripts/dev.py check all
```

On Windows, use `.venv/Scripts/python.exe`; on Unix, use `.venv/bin/python` when
the worktree environment exists. Run focused tests before broad checks. Add
integration or migration checks when the changed boundary requires them. Do not
claim a check passed because GLM reported it; mark it as independently rerun or
reported by GLM. If dependencies, services, credentials, or time prevent a check,
state that limitation and explain its effect on the decision.

Do not repair failures during a read-only review. A failing check is evidence for a
finding or an unresolved verification item. If the user later explicitly asks for
fixes, continue ordinary development in the same worktree, preserve the review
scope, and rerun the affected checks. That follow-up does not activate OpenSpec
unless the user manually selects an OpenSpec action.

## Return a review decision

Respond in the user's language with this compact structure:

1. **Review scope:** target path, branch, base SHA, HEAD, dirty/untracked state,
   and whether the complete change range was available.
2. **Findings:** P0 through P3, each tied to a file and line. Say explicitly when
   no blocking findings were identified; do not claim that no bugs exist.
3. **Verification:** commands actually run, results, GLM-reported checks not
   rerun, and blocked or unavailable checks.
4. **Integration decision:** `ready`, `ready with follow-up`, or `blocked`, with
   the reason and any required rebase or cross-worktree check.
5. **Handoff notes:** changed paths, unresolved risks, and the exact next action.

If no complete base or task scope can be established, return `blocked` for review
scope and explain the smallest evidence GLM must provide. Do not convert an
incomplete review into an approval.

This skill is for review after implementation. It does not replace
`hecate-parallel-plan`, which chooses the next independent work package, and it
does not create an OpenSpec change. OpenSpec remains opt-in through a user's
manual command or skill invocation.
