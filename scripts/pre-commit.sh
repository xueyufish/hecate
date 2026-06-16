#!/bin/bash
# Pre-commit hook: run CI checks locally before committing.
# Install: cp scripts/pre-commit.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

# Activate venv if present
VENV_DIR="$(git rev-parse --show-toplevel)/.venv"
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
fi

echo "🔍 Running pre-commit checks..."

# 1. ruff check
echo "  [1/4] ruff check..."
if ! ruff check src/hecate/ tests/ --quiet; then
    echo "❌ ruff check failed. Fix lint errors before committing."
    exit 1
fi

# 2. ruff format
echo "  [2/4] ruff format --check..."
if ! ruff format --check src/hecate/ tests/; then
    echo "❌ ruff format failed. Run 'ruff format src/hecate/ tests/' to fix."
    exit 1
fi

# 3. mypy
echo "  [3/4] mypy..."
if ! mypy src/; then
    echo "❌ mypy failed. Fix type errors before committing."
    exit 1
fi

# 4. pytest
echo "  [4/4] pytest..."
if ! python -m pytest tests/ -q --tb=short; then
    echo "❌ pytest failed. Fix failing tests before committing."
    exit 1
fi

echo "✅ All pre-commit checks passed!"
