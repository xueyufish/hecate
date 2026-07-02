#!/usr/bin/env bash
# Run pytest scoped to changed files based on dependency layer mapping.
# Optimizations:
#   1. Skip pytest for doc-only, frontend-only, config-only changes
#   2. Scope tests by dependency layer (engine->test_engine, etc.)
#   3. Use pytest-xdist for parallel execution (-n auto)
set -euo pipefail

staged=$(git diff --cached --name-only --diff-filter=ACMR)

if [ -z "$staged" ]; then
    echo "pytest: no staged files, skipping"
    exit 0
fi

# Check if only non-Python files changed (docs, frontend, configs)
python_files=$(echo "$staged" | grep -E '\.py$' || true)

if [ -z "$python_files" ]; then
    echo "pytest: no Python files changed, skipping"
    exit 0
fi

test_dirs=""

while IFS= read -r f; do
    case "$f" in
        src/hecate/core/*|alembic/*|pyproject.toml)
            echo "pytest: full suite (infrastructure change: $f)"
            exec .venv/bin/python -m pytest tests/ -q --tb=short -x -n auto
            ;;
        src/hecate/engine/*)
            test_dirs="$test_dirs tests/test_engine"
            ;;
        src/hecate/models/*)
            test_dirs="$test_dirs tests/test_models tests/test_api tests/test_services"
            ;;
        src/hecate/services/*)
            test_dirs="$test_dirs tests/test_services tests/test_api"
            ;;
        src/hecate/api/*)
            test_dirs="$test_dirs tests/test_api"
            ;;
        tests/*)
            dir=$(echo "$f" | cut -d/ -f1-2)
            test_dirs="$test_dirs $dir"
            ;;
    esac
done <<< "$python_files"

test_dirs=$(echo "$test_dirs" | tr ' ' '\n' | sort -u | grep -v '^$' | tr '\n' ' ' || true)

if [ -z "$test_dirs" ]; then
    echo "pytest: no testable changes detected, skipping"
    exit 0
fi

echo "pytest: scoped -> $test_dirs"
exec .venv/bin/python -m pytest $test_dirs -q --tb=short -x -n auto
