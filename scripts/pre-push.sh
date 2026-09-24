#!/bin/bash
# Pre-push hook: rebase against origin/main before pushing.
# Ensures every push is based on the latest main, preventing stale-branch
# conflicts when the PR is opened.
#
# Install (once per clone):
#   cp scripts/pre-push.sh .git/hooks/pre-push && chmod +x .git/hooks/pre-push
#
# Behavior:
#   1. Skip on main/master (protected; CI is the gate there).
#   2. Skip on detached HEAD.
#   3. Fetch origin.
#   4. If current branch is behind origin/main: auto-rebase.
#   5. If rebase conflicts: abort the rebase and print resolution steps.
#   6. Run the full local gate: mypy + layer-scoped pytest (xdist) over the
#      commits being pushed (merge-base..HEAD). ruff and the commit-message
#      check already ran in the pre-commit / commit-msg hooks.

set -e

# Detect current branch (skip if detached)
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null) || {
    # Detached HEAD — let the push proceed without interference
    exit 0
}

# Skip on protected branches (no rebase against self)
case "$BRANCH" in
    main|master)
        exit 0
        ;;
esac

# Fetch latest origin state quietly
git fetch origin --quiet

# Resolve refs (gracefully handle missing origin/main)
LOCAL=$(git rev-parse HEAD)
REMOTE_MAIN=$(git rev-parse --verify --quiet origin/main 2>/dev/null || echo "")
BASE=$(git merge-base HEAD origin/main 2>/dev/null || echo "")

# If origin/main doesn't exist (e.g., a fork), skip the check
if [ -z "$REMOTE_MAIN" ] || [ -z "$BASE" ]; then
    exit 0
fi

# Branch is at main tip → no-op
if [ "$LOCAL" = "$REMOTE_MAIN" ]; then
    exit 0
fi

# Branch is exactly the merge-base with main → no rebase needed
if [ "$LOCAL" = "$BASE" ]; then
    exit 0
fi

# origin/main is exactly the merge-base → branch is ahead, no rebase needed
if [ "$REMOTE_MAIN" = "$BASE" ]; then
    exit 0
fi

# Otherwise: branch has diverged from main. Check whether it's behind.
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")

if [ "$BEHIND" -gt 0 ]; then
    echo "⚠️  Branch '$BRANCH' is $BEHIND commit(s) behind origin/main."
    echo "🔄 Auto-rebasing against origin/main..."

    if ! git rebase origin/main; then
        echo ""
        echo "❌ Rebase failed due to conflicts. Push aborted."
        echo ""
        echo "   Resolution steps:"
        echo "     1. Resolve conflicts in the listed files"
        echo "     2. git add <resolved-files>"
        echo "     3. git rebase --continue"
        echo "     4. git push     # re-run push"
        echo ""
        echo "   To abort the rebase instead: git rebase --abort"
        echo ""
        exit 1
    fi

    echo "✅ Rebase complete. Proceeding with push."
fi

# Full local gate before sharing code: same checks as CI, scoped to what
# this push carries. Fast checks (ruff, commit message) already ran per commit.
VENV_DIR="$(git rev-parse --show-toplevel)/.venv"
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
else
    echo "❌ No .venv found at $VENV_DIR. Bootstrap it first (see .envrc header / AGENTS.md)."
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
PUSH_RANGE="$(git merge-base HEAD origin/main)..HEAD"

echo "🔍 Running pre-push checks..."

# 1. mypy
echo "  [1/2] mypy..."
if ! mypy src/; then
    echo "❌ mypy failed. Fix type errors before pushing."
    exit 1
fi

# 2. pytest scoped to the pushed changes
echo "  [2/2] pytest (scoped to pushed changes)..."
if ! bash "${REPO_ROOT}/scripts/smart-pytest.sh" --diff "$PUSH_RANGE"; then
    echo "❌ pytest failed. Fix failing tests before pushing."
    exit 1
fi

echo "✅ Pre-push checks passed!"

# Allow push to proceed
exit 0