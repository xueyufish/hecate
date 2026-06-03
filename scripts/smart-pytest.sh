#!/usr/bin/env bash
# Run pytest scoped to changed files based on dependency layer mapping.
#
# Layer mapping:
#   engine/    -> test_engine/
#   models/    -> test_models/ + test_api/ + test_services/
#   services/  -> test_services/ + test_api/
#   api/       -> test_api/
#   core/      -> full suite (infrastructure affects everything)
#   tests/     -> only the changed test directory
set -euo pipefail

staged=$(git diff --cached --name-only --diff-filter=ACMR)

if [ -z "$staged" ]; then
    echo "pytest: no staged files, running full suite"
    exec .venv/bin/python -m pytest tests/ -q --tb=short -x
fi

test_dirs=""

while IFS= read -r f; do
    case "$f" in
        src/hecate/core/*|alembic/*|pyproject.toml)
            echo "pytest: full suite (infrastructure change: $f)"
            exec .venv/bin/python -m pytest tests/ -q --tb=short -x
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
done <<< "$staged"

test_dirs=$(echo "$test_dirs" | tr ' ' '\n' | sort -u | grep -v '^$' | tr '\n' ' ' || true)

# Only non-testable files changed (configs, scripts, docs, web) -> skip pytest
if [ -z "$test_dirs" ]; then
    echo "pytest: no testable changes detected, skipping"
    exit 0
fi

echo "pytest: scoped -> $test_dirs"
exec .venv/bin/python -m pytest $test_dirs -q --tb=short -x
