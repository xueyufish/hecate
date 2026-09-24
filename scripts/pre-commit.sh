#!/bin/bash
# Pre-commit hook: fast checks only (target: a few seconds).
#
# Runs ruff check + ruff format --check over the tree. The commit message
# is validated by the commit-msg hook (commitizen); mypy and pytest run in
# the pre-push hook as the full local gate (scoped by scripts/smart-pytest.sh).
#
# Install (once per clone; worktrees share .git/hooks/):
#   cp scripts/pre-commit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "${SCRIPT_DIR}/prevent-main-commit.sh" ]; then
    bash "${SCRIPT_DIR}/prevent-main-commit.sh" || exit 1
fi

VENV_DIR="$(git rev-parse --show-toplevel)/.venv"
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
else
    echo "❌ No .venv found at $VENV_DIR. Bootstrap it first (see .envrc header / AGENTS.md)."
    exit 1
fi

echo "🔍 Running pre-commit checks (fast)..."

# 1. ruff check
echo "  [1/2] ruff check..."
if ! ruff check src/hecate/ tests/ --quiet; then
    echo "❌ ruff check failed. Fix lint errors before committing."
    exit 1
fi

# 2. ruff format
echo "  [2/2] ruff format --check..."
if ! ruff format --check src/hecate/ tests/; then
    echo "❌ ruff format failed. Run 'ruff format src/hecate/ tests/' to fix."
    exit 1
fi

echo "✅ Fast pre-commit checks passed! (commit-msg check + mypy + pytest run later in the pipeline)"
