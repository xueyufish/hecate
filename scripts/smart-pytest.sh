#!/usr/bin/env bash
# Run pytest scoped to changed files based on dependency layer mapping.
# Optimizations:
#   1. Skip pytest for doc-only, frontend-only, config-only changes
#   2. Scope tests by domain directory (runtime->test_runtime, etc.);
#      test dirs keep legacy names (e.g. test_auth covers enterprise/auth)
#   3. Use pytest-xdist for parallel execution (-n auto)
#
# Change source:
#   (no argument)   staged files (git diff --cached)
#   --diff <range>  commits in <range>, e.g. merge-base..HEAD (pre-push gate)
set -euo pipefail

if [ "${1:-}" = "--diff" ]; then
    range="${2:?--diff requires a commit range, e.g. base..HEAD}"
    changed_files=$(git diff --name-only --diff-filter=ACMR "$range")
else
    changed_files=$(git diff --cached --name-only --diff-filter=ACMR)
fi

if [ -z "$changed_files" ]; then
    echo "pytest: no changed files, skipping"
    exit 0
fi

# Check if only non-Python files changed (docs, frontend, configs)
python_files=$(echo "$changed_files" | grep -E '\.py$' || true)

if [ -z "$python_files" ]; then
    echo "pytest: no Python files changed, skipping"
    exit 0
fi

# venv layout differs per platform: bin/ on macOS+Linux, Scripts/ on Windows
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    PY=".venv/Scripts/python.exe"
fi
if [ ! -x "$PY" ]; then
    echo "pytest: no .venv interpreter found (looked for .venv/bin/python, .venv/Scripts/python.exe)"
    exit 1
fi

test_dirs=""

while IFS= read -r f; do
    case "$f" in
        src/hecate/core/*|src/hecate/main.py|src/hecate/cli/*|alembic/*|pyproject.toml|packages/*)
            echo "pytest: full suite (infrastructure/composition/wheel change: $f)"
            exec "$PY" -m pytest tests/ -q --tb=short -x -n auto
            ;;
        src/hecate/runtime/*)
            test_dirs="$test_dirs tests/test_runtime"
            ;;
        src/hecate/enterprise/*)
            test_dirs="$test_dirs tests/test_enterprise tests/test_auth tests/test_vault tests/test_budget tests/test_api tests/test_services"
            ;;
        src/hecate/channel/*)
            test_dirs="$test_dirs tests/test_channel tests/test_channels tests/test_a2a tests/test_gateway tests/test_api tests/test_services"
            ;;
        src/hecate/tools/*)
            test_dirs="$test_dirs tests/test_mcp tests/test_skill_registry tests/test_plugin tests/test_api tests/test_services"
            ;;
        src/hecate/studio/*)
            test_dirs="$test_dirs tests/test_api tests/test_services"
            ;;
        src/hecate/ops/*)
            test_dirs="$test_dirs tests/test_observability tests/test_ops_center tests/test_api tests/test_services"
            ;;
        src/hecate/models/*)
            test_dirs="$test_dirs tests/test_models tests/test_api tests/test_services"
            ;;
        src/hecate/api/*)
            test_dirs="$test_dirs tests/test_api"
            ;;
        tests/conftest.py)
            echo "pytest: full suite (shared fixture change: $f)"
            exec "$PY" -m pytest tests/ -q --tb=short -x -n auto
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
exec "$PY" -m pytest $test_dirs -q --tb=short -x -n auto
