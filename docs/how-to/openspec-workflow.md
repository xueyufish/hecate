# How to Use OpenSpec for Change Management

OpenSpec is Hecate's change management workflow. Every feature, refactor, and breaking change goes through the same nine-phase lifecycle: explore, worktree, propose, apply, archive, catalog, push, merge.

This guide explains the full lifecycle, where each file lives, how git tracks it, and the two catalog-update patterns (same PR vs separate branch).

---

## Prerequisites

- `git` configured with push access to `origin`
- The OpenSpec CLI installed (ships with the repo at `scripts/opsx-flow.sh`)
- Familiarity with the `OpenSpec` slash commands: `/opsx-explore`, `/opsx-propose`, `/opsx-apply`, `/opsx-archive`

> **Note**: OpenSpec slash commands are **user-triggered only**. The AI agent will not invoke them automatically — see `AGENTS.md` § "OpenSpec commands MUST be triggered by the user manually".

---

## The 9-phase lifecycle

This is the **single canonical flow**. Every OpenSpec change goes through all 9 phases; the catalog update is committed on the same branch as the archive and lands in the same PR.

```
Phase 1: Explore (main)
         ↓
Phase 2: Worktree
         ↓
Phase 3: Optional deeper explore (worktree)
         ↓
Phase 4: Propose (worktree)
         ↓
Phase 5: Apply (worktree)
         ↓
Phase 6: Archive (worktree)         ← /opsx-archive: spec sync + mv + commit
         ↓
Phase 7: Catalog update (worktree)  ← update catalog files, commit on same branch
         ↓
Phase 8: Push (worktree)            ← single git push carries all commits
         ↓
Phase 9: PR + Merge                 ← single PR merges everything to main
```


## Phase 1 — Explore (main checkout)

Use `/opsx-explore <topic>` (or a free-form chat) to think through the change. Capture intermediate conclusions in `openspec/changes/<name>/`:

```
openspec/changes/<name>/
├── decisions.md    ← explore workspace, append-only, frozen after proposal lands
└── (notes, sketches, etc.)
```

Files in this phase are **uncommitted** in the main checkout. `scripts/opsx-flow.sh start` detects them and **syncs them into the worktree** as a rescue mechanism (see AGENTS.md § "OpenSpec change file sync").

> **Note**: in early-development phases when the change name and shape are still fluid (e.g. scoping a P3 feature for the first time), Phase 1 may run in the main checkout. Artifacts are untracked; `git status` shows them as "Untracked files", and `main`'s HEAD is unchanged. Once the change name is locked, prefer running Phase 1 directly in the worktree — the rescue mechanism exists for the fluid case, not as the default path. The artifacts you wrote in main end up on a `feat/<name>` branch after `start` runs.

> **Tip**: when Phase 1 produces a solid shape, **immediately** move to Phase 2. Do not run `/opsx-propose` in the main checkout — the AGENTS.md rule is "**Always run OpenSpec work inside a worktree**". If you accidentally run it in main, the worktree start will rescue your uncommitted artifacts.

---

## Phase 2 — Create worktree

```bash
./scripts/opsx-flow.sh start <change-name>
```

What the script does:

1. Resolves `HECATE_WORKTREE_ROOT` (default: `${REPO_ROOT}/../.worktrees/`)
2. Detects uncommitted `openspec/changes/<change-name>/` artifacts in main and **syncs them into the worktree** (only if the worktree copy does not already exist)
3. Checks whether `feat/<change-name>` exists:
   - **Exists** → `git worktree add <wt_path> <branch>` (reuse the branch)
   - **Does not exist** → `git worktree add -b <branch> <wt_path> main` (create new branch from main)
4. Copies files listed in `.worktreeinclude` (if present) into the worktree
5. `exec opencode` inside the worktree directory (launches a new session)

After this command finishes you are in a **new opencode session** rooted at the worktree. Phase 1's uncommitted artifacts (e.g. `decisions.md`) are now visible inside `openspec/changes/<change-name>/` of the worktree.

**Automatic cleanup of main checkout**: when the sync genuinely copies uncommitted artifacts into a freshly created worktree, the script also removes the main-checkout copy. Three safety checks gate the removal:

1. The worktree copy exists
2. Both copies contain `.openspec.yaml` (the OpenSpec change sentinel)
3. `diff -rq` shows the two directories are byte-identical

If any check fails, the cleanup is skipped with a diagnostic — the developer must then `rm -rf openspec/changes/<name>` in the main checkout themselves once they verify nothing was lost.

---

## Phase 3 — Optional deeper explore (worktree)

The new session loads the worktree's `AGENTS.md`, `.opencode/skills/`, and any synced artifacts (including `decisions.md` from Phase 1). If you need more thinking before proposing:

```bash
/opsx-explore <deeper-topic>
```

Explore is optional — Phase 4 can run directly.

---

## Phase 4 — Propose (worktree)

```bash
/opsx-propose <change-name>
```

The skill runs:

1. `openspec new change "<name>"` — if `.openspec.yaml` is missing, scaffold `openspec/changes/<name>/` with empty artifacts
2. `openspec status --change "<name>" --json` — get the artifact build order
3. For each artifact with satisfied dependencies:
   - `openspec instructions <id> --change "<name>" --json` — get the template + constraints
   - Read any completed dependency files (including `decisions.md`)
   - Write the artifact using the template structure
4. Verify with `openspec status --change "<name>"` that every `applyRequires` artifact is `done`

Output structure:

```
openspec/changes/<change-name>/
├── .openspec.yaml
├── decisions.md          ← Phase 1 work area, now historical
├── proposal.md           ← contract (what & why)
├── design.md             ← design (how)
├── tasks.md              ← implementation steps
└── specs/
    └── <capability>/spec.md   ← delta spec (if applicable)
```

After this phase, `decisions.md` has done its job — the **contract** lives in `proposal.md`. Future explore goes into `design.md`'s Decisions Log section (see Phase 5), not into `decisions.md`.

---

## Phase 5 — Apply (worktree)

```bash
/opsx-apply <change-name>
```

The skill reads `tasks.md` and executes tasks sequentially. **Do not delegate `/opsx-apply` to background agents** (AGENTS.md rule).

For each task:

1. Edit code under `src/hecate/`, `web/`, etc.
2. Write tests under `tests/`
3. Run the four CI checks: `ruff check`, `ruff format --check`, `mypy src/`, `pytest tests/`
4. `git add` + `git commit` (one commit per task)
5. Mark the task `[x]` in `tasks.md`

**If apply reveals new design questions**, capture them in `design.md` (not `decisions.md`):

```markdown
## Decisions Log

### 2026-08-12: ChannelABC type signature
Originally `ChannelABC.receive(raw: object)`. While implementing
the first concrete adapter, we needed structured payloads
(event metadata, tenant tokens), so we narrowed to
`receive(raw: dict[str, object])`. Tracked in commit aaf5415.
```

**If apply changes the scope**, update `proposal.md` and record the change in the commit message:

```
feat(channel): 11.3 feishu - ChannelABC adapter

Update proposal.md scope: drop 11.3.1 token refresh, defer to 11.3.2.
Per decisions log 2026-08-12.
```

---

## Phase 6 — Archive (worktree — mandatory)

This phase handles **the archive commit**.

`/opsx-archive` **must run inside the worktree**, never in the main checkout. The reason: `main` is a protected branch (AGENTS.md § Git) — committing the archive `mv` directly on `main` would violate that rule. Archive commit must ride through the same worktree branch as the feature and merge via PR.

```bash
# Still in the worktree from Phase 5:
/opsx-archive <change-name>
```

What the skill does:

1. Run `openspec list --json` — if no change given, prompt for selection
2. Check `openspec status --change "<name>" --json` — warn on incomplete artifacts
3. Read `tasks.md` — warn on incomplete tasks
4. **Delta spec sync assessment** — compare `openspec/changes/<name>/specs/` against `openspec/specs/<capability>/spec.md`. Prompt: *"Sync now (recommended)"* vs *"Archive without syncing"*. Always choose Sync now unless you have a specific reason. (The sync, when chosen, adds an extra `docs(spec): sync delta specs` commit before the archive commit.)
5. `mkdir -p openspec/changes/archive && mv openspec/changes/<name> openspec/changes/archive/YYYY-MM-DD-<name>`
6. `git add` + `git commit` **inside the worktree** — creates `chore(archive): mv to archive/...` on the same `feat/<change-name>` branch

Output structure:

```
openspec/changes/archive/YYYY-MM-DD-<change-name>/
├── .openspec.yaml
├── proposal.md
├── design.md
├── tasks.md
├── decisions.md
└── specs/<capability>/spec.md
```

After `/opsx-archive` finishes:

- The worktree has one or two additional commits on `feat/<change-name>`:
  - `docs(spec): sync delta specs` (only if step 4 chose Sync now)
  - `chore(archive): mv to archive/...`
- Phase 7 (catalog) handles the catalog next

---

## Phase 7 — Catalog update (worktree — same branch)

After Phase 6 (archive), still in the same worktree, update **the catalog files** that the change touches and commit on the same branch. This keeps the catalog update in the same PR as the feature and archive commits — one push, one review, one merge.

### Which catalog files exist (post-PR #63)

Since PR #63 (`docs: add OpenSpec workflow guide and strengthen main branch protection`), the previously `.gitignored` working drafts under `docs/features/` are **git-tracked**. All four files now ride the normal PR flow:

| File | Role |
|---|---|
| `docs/design/positioning.md` | Canonical competitive positioning + feature highlights. The single file AGENTS.md § "Catalog & Roadmap sync is MANDATORY" calls out by name. Update here for any feature that changes the positioning narrative. |
| `docs/features/feature-catalog.md` | Full feature inventory: P1→P5 priority list, status counts, dependency notes, status-bar counters at the top. Update here for any feature that shifts the priority counts or lands in a new priority tier. |
| `docs/features/roadmap.md` | Sprint-level schedule and ownership. Update here if the change moves a sprint boundary, adds a new workstream, or changes ownership. |
| `docs/features/p3-mvp-audit.md` | P3 audit statistics — completed/in-progress/zero-code counts per sprint. Update here whenever the audit rolls forward. |

Pick the file(s) the change actually affects. A bug fix that lands as P3 likely touches only `feature-catalog.md` and `p3-mvp-audit.md`. A new strategic positioning claim touches `positioning.md`. Large features may touch all four. If nothing in the change belongs in any catalog, skip the commit (the catalog step is no-op).

```bash
# Still in the worktree from Phase 6:
$EDITOR docs/features/feature-catalog.md    # pick the file(s) the change touches
git add docs/features/feature-catalog.md
git commit -m "docs(catalog): mark <feature> as shipped in P3"
```

`feat/<change-name>` branch now contains (in chronological git log order):

```
├─ commit: feat: implement task 1
├─ commit: feat: implement task 2
├─ commit: docs(spec): sync delta specs          (Phase 6 step 4 — only if Sync now chosen)
├─ commit: chore(archive): mv to archive/...    (Phase 6 step 5-6)
└─ commit: docs(catalog): mark <feature> ...    (this phase; one commit per file touched)
```

> **Why all four catalog files are git-tracked now**: before PR #63, `docs/features/` was excluded by `.gitignore`. Worktree ↔ main sync of catalog edits depended on ad-hoc copy. PR #63 removed the exclusion so catalog edits ride the normal worktree → push → PR → merge flow alongside feature code. See the PR description for context.

---

## Phase 8 — Push (worktree)

```bash
./scripts/opsx-flow.sh push <change-name>
```

What the script does:

1. Checks `origin` remote exists
2. `git push -u origin feat/<change-name>`

The push is the single operation in the canonical 9-phase flow that **always requires explicit user confirmation in chat** (per AGENTS.md § Git) — even when running `opsx-flow.sh push` rather than raw `git push`. If you split the catalog into its own branch (see the Phase 7 exception note), that second push also requires confirmation.

---

## Phase 9 — PR + merge

1. Open a PR on GitHub: `feat/<change-name>` → `main` (via `gh pr create` or the web UI)
2. CI (ruff + ruff format + mypy + pytest) must pass
3. Reviewer approves
4. **Merge on GitHub** by clicking "Merge pull request" — this is a server-side merge and does **not** require switching to the main checkout or running `git merge` locally

After the merge:

- `main` now contains the final tracked state of `openspec/changes/<change-name>/` plus the catalog update from `docs/design/positioning.md`
- `feat/<change-name>` worktree can be safely deleted (see [Cleanup](#cleanup))

> **Note**: if your environment cannot use GitHub PRs (e.g. self-hosted without GitHub), the fallback is to switch to the main checkout and run `git merge --no-ff feat/<change-name>` followed by `git push origin main`. But under GitHub Flow this local merge path is intentionally **not** used — see `AGENTS.md` § Git.

---

## Cleanup

After Phase 9:

```bash
./scripts/opsx-flow.sh delete <change-name>
```

This removes the worktree directory and the `feat/<change-name>` branch in one command. Safe because all commits are already on `main`.

> **Exception**: if your catalog edit was unusually large and warrants an independent review, you may have extracted it into a separate `chore-positioning-update-<name>` branch — see the Phase 7 note. In that case the catalog PR merges **after** the delete step above, and the worktree deletion should happen for the catalog branch instead.

---

## File lifecycle table

| File | Phase created | Phase frozen | Mutability |
|------|---------------|--------------|------------|
| `decisions.md` | Phase 1 explore | After Phase 4 propose | Append-only history |
| `.openspec.yaml` | Phase 4 propose | — | Frozen at creation |
| `proposal.md` | Phase 4 propose | Phase 4 propose complete | Read-only after propose; updates require commit message explaining scope change |
| `design.md` | Phase 4 propose | After Phase 5 apply | Editable in Phase 5; freeze in archive |
| `tasks.md` | Phase 4 propose | After Phase 5 apply | Editable in Phase 5 (tick boxes); freeze in archive |
| `specs/<cap>/spec.md` | Phase 4 propose | After Phase 6 archive | Editable in Phase 5; merged into main spec by `/opsx-archive` |

---

## Git history table

```
aaf5415 ─── main
   │
   ├── feat/<change-name>                       (Phase 2)
   │      │
   │      ├─ commit: chore: add proposal/design/tasks       (Phase 4)
   │      ├─ commit: feat: implement task 1               (Phase 5)
   │      ├─ commit: feat: implement task 2               (Phase 5)
   │      ├─ commit: docs(spec): sync delta specs          (Phase 6 step 4 — only if Sync now chosen)
   │      ├─ commit: chore(archive): mv to archive/...    (Phase 6 step 5-6)
   │      └─ commit: docs(catalog): mark <feature> ...     (Phase 7 — see Phase 7 table for which file)
   │             ↓
   │             (PR merge) → main has archive/ tracked + catalog updated
```

`git log --follow` on any file in the archive directory shows its full history from creation to archive — git handles `mv` as a rename.

> **Deprecated**: the previous table included a "main-checkout archive" branch (running `/opsx-archive` in the main checkout). That pattern is no longer allowed — see [Archive constraints](#archive-constraints).

---

## Archive constraints

Two rules make archive workflow stricter than feature work:

1. **`/opsx-archive` must run inside the worktree** — never in the main checkout. The `mv` and the `git add` + `git commit` that follow both happen on the `feat/<change-name>` branch. The commit rides through the same PR as the feature code.
2. **Catalog updates must go through a branch** — see [Phase 7](#phase-7--catalog-update-worktree--same-branch). AGENTS.md § "Catalog & Roadmap sync is MANDATORY" requires updating the relevant catalog file(s) (`docs/design/positioning.md` and/or files under `docs/features/`); this too must commit on a non-`main` branch.

Both rules derive from the same constraint: `main` is a protected branch (AGENTS.md § Git). Any operation that would otherwise be performed in the main checkout must instead be performed on a feature branch and merged via PR.

The previous version of this guide described a "main-checkout archive" workflow. **That workflow is now deprecated** — it was a documentation artifact from before `main` was explicitly protected. Use the canonical 9-phase flow instead.

---

## Common pitfalls

### Pitfall 1: Archive files appear to be lost after worktree deletion

**Symptom**: you archive in the worktree, merge to main, then delete the worktree. You worry the archive files are gone.

**Why it's fine**: `git mv` is atomic, and PR merge copies the commit into `main`'s object database. Deleting the worktree directory and `feat/<change-name>` branch only removes **references**, not the commit. The archive lives permanently in `main`'s history.

**Verify**:
```bash
# From main checkout:
git log --follow openspec/changes/archive/YYYY-MM-DD-<change-name>/proposal.md
# Should show full history from creation to archive.
```

### Pitfall 2: Delta spec not synced to main specs

**Symptom**: you archive without syncing, but the spec changes for `<capability>` aren't reflected in `openspec/specs/<capability>/spec.md`.

**Fix**: in `/opsx-archive` Step 4, choose **"Sync now (recommended)"** when prompted.

### Pitfall 3: Catalog not updated after archive

**Symptom**: the feature is archived but the catalog files still describe the old status (e.g. "in progress" instead of "shipped", or counts out of date).

**Why**: `/opsx-archive` does not automatically update the catalog. Per AGENTS.md § "Catalog & Roadmap sync is MANDATORY", you must update the relevant catalog file(s) in Phase 7 (or in a follow-up branch if you split the catalog into its own PR). Since PR #63 all four catalog files are git-tracked — see [Phase 7](#phase-7--catalog-update-worktree--same-branch) for which file to pick.

### Pitfall 4: Two changes in parallel with conflicting changes/<name>

**Symptom**: two worktrees open for `web-widget-access` and `multi-channel-feishu-slack`. Both modify `openspec/changes/<name>/` (different `<name>`). Both PRs merge.

**Why it's fine**: different `<name>` directories don't conflict. Files like `catalog.md` may conflict — resolve in the second PR's merge.

### Pitfall 5: worktree created on stale main

**Symptom**: `./scripts/opsx-flow.sh start <name>` created the branch from a `main` HEAD that was 5 days old.

**Fix**: after start, in the worktree run:
```bash
git fetch origin
git rebase origin/main
```

---

## Quick reference

```bash
# Create
./scripts/opsx-flow.sh start <change-name>

# Push
./scripts/opsx-flow.sh push <change-name>

# Delete (worktree + branch after merge)
./scripts/opsx-flow.sh delete <change-name>

# Delete (worktree only, keep branch)
./scripts/opsx-flow.sh remove <change-name>

# List worktrees
./scripts/opsx-flow.sh list
```

OpenSpec slash commands:

```
/opsx-explore <topic>           # think through a change (optional)
/opsx-propose <change-name>     # generate proposal.md / design.md / tasks.md
/opsx-apply <change-name>       # implement tasks from tasks.md (main agent only)
/opsx-archive <change-name>     # mv to archive/, sync delta specs (worktree, before push)
```

---

## Related

- **`AGENTS.md` § OpenSpec workflow** — full agent-side rules and constraints
- **`scripts/opsx-flow.sh`** — the only entry point for worktree lifecycle
- **`scripts/worktree-help.sh`** — underlying worktree creation / sync logic
- **`.opencode/skills/openspec-propose/SKILL.md`** — proposal phase internals
- **`.opencode/skills/openspec-archive/SKILL.md`** — archive phase internals
- **`docs/how-to/version-and-rollback-agent.md`** — analogous workflow for runtime versioning