---
name: hecate-glm-review
description: Review and repair a completed GLM implementation in an existing Hecate worktree against its stated goal and base revision. Use when GLM hands a branch back to Codex for verification, regression analysis, direct fixes, and integration readiness. Produces findings, repairs, and evidence; it does not create worktrees or branches, commit, push, merge, or invoke OpenSpec by itself.
license: MIT
---

# Hecate GLM review and repair

Review the implementation in the existing worktree where GLM finished the task.
Treat the GLM handoff as evidence to verify, then repair confirmed findings in
that same worktree and branch. The default result is a read-and-fix review that
leaves the target branch ready for the user or integrator to inspect.

## Establish the review target

Use the worktree path, branch, base revision, task description, and GLM report from
the user. If a path is missing, inspect registered worktrees with:

```bash
git worktree list --porcelain
```

Choose a target only when its branch and task can be identified uniquely. Never
silently review or edit a different checkout. A path under a requested worktree
directory that is not registered by Git is evidence to report, not a branch to
guess.

Before any write, confirm that the target is the GLM implementation branch, is a
registered Git worktree, and is not `main` or another protected integration branch.
If the target is detached, points at `main`, or cannot be identified, stop before
editing and return `blocked` with the exact reason. Do not create a replacement
worktree or branch to work around an invalid target.

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

Report each finding internally with the exact file and line, observed evidence,
impact, and a concrete remediation direction. Do not report style preferences as
defects when repository tools already enforce the convention. Separate confirmed
findings from questions and residual risk. If the task is underspecified, identify
the missing acceptance decision instead of inventing one.

## Repair confirmed findings in place

When this skill is explicitly invoked for a completed GLM implementation, repair
confirmed findings directly in the identified GLM worktree and branch. Do not ask
for per-finding confirmation. Keep repairs within the stated task and the findings
required to make that task correct.

The repair boundary is strict:

- Edit only the identified GLM worktree; never edit another worktree or the main checkout.
- Do not run `git checkout`, `reset`, `clean`, `stash`, `rebase`, merge, or commands that discard or relocate work.
- Do not create a worktree or branch. Do not switch branches.
- Do not commit, amend, push, merge, publish, open a pull request, or send messages.
- Do not invoke OpenSpec or create OpenSpec change artifacts unless the user separately invokes an OpenSpec action.

Fix P0-P3 findings when the expected behavior and remediation are sufficiently
clear. If a finding requires a product decision, broad redesign, ownership change,
destructive migration, or edits outside the GLM task, stop that repair and report
it as unresolved instead of guessing. A failing check may be fixed in place when
the cause is within the task; investigate before labeling it pre-existing.

After each logical repair:

1. Inspect the resulting diff and confirm it remains inside the target scope.
2. Run the smallest relevant focused test or check.
3. Re-review callers, error paths, security boundaries, and regression coverage.
4. Continue with the next confirmed repair only if the target remains coherent.

Preserve GLM's existing changes and worktree state. Do not rewrite its commits or
silently discard untracked files. Leave the worktree with the repairs visible for
the user or integrator to review and commit.

## Verify the repaired implementation

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

If a verification failure remains after a reasonable in-scope repair attempt,
report the failure as unresolved with its command and evidence. Do not keep making
speculative changes merely to obtain a green check.

## Return a review and repair decision

Respond in the user's language with this compact structure:

1. **Review scope:** target path, branch, base SHA, HEAD, dirty/untracked state,
   and whether the complete change range was available.
2. **Findings before repair:** P0 through P3, each tied to a file and line. Say
   explicitly when no findings were identified; do not claim that no bugs exist.
3. **Repairs applied:** each repaired finding, changed file, behavior change, and
   any intentionally untouched finding with its reason.
4. **Verification:** commands actually run, results, GLM-reported checks not
   rerun, and blocked or unavailable checks.
5. **Integration decision:** `ready`, `ready with follow-up`, or `blocked`, with
   the reason and any required rebase or cross-worktree check.
6. **Handoff notes:** changed paths, final dirty state, unresolved risks, and the
   exact next action. State explicitly that no commit or push was performed.

If no complete base or task scope can be established, return `blocked` before
editing. If a target is valid but a finding needs a material product or ownership
decision, apply the safe in-scope repairs and return `ready with follow-up` or
`blocked` for the unresolved decision.

This skill reviews and repairs a completed GLM implementation. It does not choose
the next parallel work package, create an OpenSpec change, or authorize push or
merge. OpenSpec remains opt-in through a user's manual command or skill
invocation.
