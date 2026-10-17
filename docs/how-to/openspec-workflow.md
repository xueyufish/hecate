# OpenSpec development workflow

Use an isolated worktree for each change. The repository scripts own environment and check commands; OpenSpec owns proposal, design, delta specs and implementation tasks. Root [AGENTS.md](../../AGENTS.md) owns repository policy.

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

The script creates the branch/worktree and synchronizes its development environment. It initializes commands for the selected Coding Agent; `--tools codex` selects Codex only, and `--no-tools` keeps existing checked-in skills without regenerating them. `HECATE_WORKTREE_ROOT` overrides the default worktree parent.

The user triggers `/opsx-propose <change-name>` to prepare proposal, design, specs and tasks, then `/opsx-apply <change-name>` to implement. Natural-language requests can explicitly authorize those phases. Work from the change artifacts and related code; investigate material scope changes before expanding them.

## Implement and verify

Complete a behavior and its relevant regression evidence together. Mark its task complete immediately after the specified behavior is implemented and verified. Organize commits by coherent, reviewable changes; a task checkbox does not require its own commit. The main agent implements Apply directly.

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

After merge, the user triggers `/opsx-archive <change-name>` against an up-to-date feature worktree. Check positioning.md and prune obsolete AGENTS.md guidance before the archive move. Synchronize any delta specs and submit archive changes through a branch and PR; do not edit or commit directly on main.

Archive is not a prerequisite for the implementation PR. Keep required catalog updates with their behavior; a large independent catalog change can use a separate focused PR. When all work on a worktree has merged and its state is preserved, use `opsx-flow.sh delete <change-name>` for cleanup. Review local changes before deleting a worktree.
