# Development workflow and optional OpenSpec

Use an isolated worktree for each coherent goal. Develop directly by default. Root [AGENTS.md](../../AGENTS.md) owns the activation and authorization rules.

## Default development

Ordinary implementation, fixes, refactors, analysis, and design requests do not invoke OpenSpec commands or skills and do not require a change directory, proposal, delta spec, tasks file, or archive. This applies equally to APIs, security and tenant isolation, migrations, Runtime, architecture, CI/release gates, cross-domain work, and uncertain requirements. An existing OpenSpec change or a mention of OpenSpec does not activate the workflow.

Investigate the relevant code and contracts, maintain a useful plan for long tasks, resolve material questions with the user, implement, and verify. Update existing specifications and design documents directly when their behavior changes; do not create delta specs merely to update documentation. Keep acceptance evidence proportional to the behavior and risk. Tests, shared static checks, CI, and PR review remain required where applicable.

## Manually activate OpenSpec

Enter OpenSpec only when the user manually invokes the corresponding slash command, named skill, or explicitly requests execution of a CLI command. A request such as "design a solution" or a discussion about OpenSpec is not an invocation. Follow-ups may continue an already selected action without re-entering its command, but do not implicitly select another action.

| User-selected action | Scope |
| --- | --- |
| `/opsx-propose <name>` or `$openspec-propose` | Generate the selected schema's planning artifacts, then stop |
| `/opsx-apply <name>` or `$openspec-apply-change` | Read required artifacts, implement tasks, and run affected verification |
| `/opsx-sync <name>` or `$openspec-sync-specs` | Synchronize the selected change's delta specs to main specs |
| `/opsx-archive <name>` or `$openspec-archive-change` | Check completion, synchronize applicable deltas, and archive |
| `/opsx-explore` or `$openspec-explore` | Explore without implementation; capture only explicitly requested planning artifacts |

The `/openspec-*` skill aliases are equivalent where supported. A CLI inspection request authorizes only that inspection, not a write workflow. Commands keep their artifact dependencies, validation, store/path selection, and task/sync checks. A normal implementation request does not authorize automatic propose, apply, sync, or archive. The user can explicitly request a sequence of actions in a single message; execute that sequence without requiring repeated messages.

Once OpenSpec is selected, use the configured `spec-driven` schema unless the user selects another schema, such as `openspec new change <name> --schema lightweight`. Lightweight records concise intent and checkable tasks; it does not define product delta specs. Resolve a schema that cannot express the requested contract with the user rather than silently skipping required artifacts. Change risk alone never activates OpenSpec.

## Development commands

Install [uv](https://docs.astral.sh/uv/), then synchronize from a repository checkout:

```bash
# Works from PowerShell, Git Bash and POSIX shells; no activated venv required.
uv run --no-project --python 3.12 scripts/dev.py sync
```

With Git Bash or a POSIX shell, the equivalent entrypoint is `bash scripts/dev.sh sync`. Once Python is available, `python scripts/dev.py` accepts the same subcommands. Every command resolves the repository from the script location and uses that worktree's `.venv`.

| Command | Purpose |
| --- | --- |
| `bash scripts/dev.sh sync` | Synchronize the declared development workspace from uv.lock |
| `bash scripts/dev.sh sync --check` | Check synchronization without installing |
| `bash scripts/dev.sh sync --profile postgres` | Include the declared PostgreSQL test driver |
| `bash scripts/dev.sh sync --profile lint` | Prepare only lint/documentation tools for a dedicated lint environment |
| `bash scripts/dev.sh doctor` | Inspect environment and hook readiness without repairing it |
| `bash scripts/dev.sh check lint` | Run the shared lint scope |
| `bash scripts/dev.sh check format` | Check formatting in the same scope |
| `bash scripts/dev.sh check types` | Type-check platform and workspace packages |
| `bash scripts/dev.sh check all` | Run the static checks, stopping on failure |
| `bash scripts/dev.sh test tests/test_scripts/ -q` | Run explicitly selected pytest targets and options |
| `bash scripts/dev.sh hooks install` | Install worktree-aware Git hook dispatchers |
| `bash scripts/dev.sh hooks check` | Verify Git will use those dispatchers |

The lint profile is for dedicated lint environments, such as the CI lint job; it replaces the development package selection. Use the development or PostgreSQL profile when returning to implementation. Sync uses `--locked`: after an intentional dependency edit, update and review `uv.lock` with `uv lock` before syncing.

Direnv calls the same synchronization entrypoint and watches dependency manifests and the lockfile. Set `HECATE_DEV_PROFILE=postgres` in the shell before direnv activation to retain the PostgreSQL profile; the default is `dev`. Without direnv, activate `.venv/bin/activate` on POSIX or `.venv/Scripts/activate` in Git Bash. PowerShell users can keep calling `python scripts/dev.py` without activation because checks select the worktree interpreter themselves.

## Git hooks

Install once per clone. Linked worktrees share the hook directory, while each dispatcher finds the invoking worktree and runs its tracked script:

```bash
bash scripts/dev.sh hooks install
# To migrate existing copied or custom hooks, review them, then preserve and replace:
bash scripts/dev.sh hooks install --replace
bash scripts/dev.sh doctor
```

The installer honors `core.hooksPath`, refuses silent replacement of unmanaged hooks, and preserves explicitly replaced hooks beside their originals with a `.before-hecate` suffix. It refuses to overwrite an existing different backup. To roll back an installation, restore the saved hook and its executable permission. Custom hook behavior is not chained automatically.

Pre-commit rejects commits on main and runs lint/format. Commit-msg validates Conventional Commits. Pre-push rebases onto origin/main as needed, then runs the shared type check. It does not run pytest. Never bypass hooks; fix the failure. A rewritten base can invalidate earlier verification: inspect the resulting diff and rerun the affected checks before treating the PR as ready.

## Start a change

```bash
./scripts/opsx-flow.sh start <change-name>
cd ../.worktrees/<change-name>
```

The script creates the branch/worktree and synchronizes its development environment. By default it keeps checked-in Agent skills and commands without running `openspec init`; direct work therefore does not create an OpenSpec root as a side effect. `--no-tools` remains an explicit equivalent. Use `--tools codex` only when you want to regenerate Codex integration, and review the resulting diff because generation can overwrite repository customizations. `HECATE_WORKTREE_ROOT` overrides the default worktree parent.

Work directly from the code, relevant contracts, and the requested goal. Starting a worktree does not invoke any OpenSpec action. If the user manually selects OpenSpec, prepare the selected schema's required artifacts before apply and mark tasks complete only after their specified acceptance checks pass. A coherent change can cover related endpoints and tasks with a shared design and acceptance matrix; independent purposes still need separate PRs.

## Implement and verify

Complete a behavior and its relevant regression evidence together. If an OpenSpec task file is in use, mark its task complete immediately after the specified behavior is implemented and verified. Organize commits by coherent, reviewable changes; a task checkbox does not require its own commit. The main agent implements Apply directly.

```bash
bash scripts/dev.sh check all
bash scripts/dev.sh test <affected-test-paths> -q
```

Use focused tests during development. `pytest --testmon` is an optional inner-loop accelerator; its first run builds the dependency database. Select dynamic-loading, subprocess, migration and real-entry tests explicitly when the change reaches them. Full pytest and the platform matrix run in CI. Repeat a passing check when changes invalidate its evidence, rather than merely because commit or push follows.

Record exact commands and observed results in the change's verification record or PR. Skipped tests, missing credentials and pending CI remain explicit limitations. Update affected documentation and catalogs in the same PR as their behavior.

## Push, review and merge

Push requires explicit user confirmation in chat, including when using the wrapper:

```bash
./scripts/opsx-flow.sh push <change-name>
```

Keep each PR focused on one purpose. Fix CI failures and review findings, and let the merge queue validate the PR against the current main. Use rebase or squash merge, following repository policy.

## Archive and cleanup

Ordinary development has nothing to archive. For a manually selected OpenSpec change, archive only when the user invokes the archive action or explicitly includes it in an action sequence. Follow the archive completion and delta-sync checks. Review `positioning.md` when product positioning or catalog text changed, and prune `AGENTS.md` when repository rules changed. Keep behavior and relevant main-spec updates reviewable together.

Archive does not certify CI, review, or merge success; report pending states and failed or skipped checks truthfully. Resolve incomplete tasks, failed required checks, or sync conflicts within scope; exceptional archival needs the user's explicit decision. Subsequent review fixes update the relevant records and rerun affected verification.

Push requires explicit chat confirmation. OpenSpec activation or archival does not authorize push, merge, publishing, or worktree deletion. After all work has merged and its state is preserved, `opsx-flow.sh delete <change-name>` is available for requested cleanup; review local changes first. Never edit or commit directly on main.
