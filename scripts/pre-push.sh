#!/usr/bin/env bash
# Rebase topic branches when needed, then run the shared type check.
# Install dispatchers with: bash scripts/dev.sh hooks install
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
branch="$(git symbolic-ref --short HEAD 2>/dev/null || true)"

if [[ -n "${branch}" && "${branch}" != "main" && "${branch}" != "master" ]] && \
        git remote get-url origin >/dev/null 2>&1; then
    git fetch origin --quiet
    if git rev-parse --verify --quiet origin/main >/dev/null; then
        behind="$(git rev-list --count HEAD..origin/main)"
        if [[ "${behind}" -gt 0 ]]; then
            echo "Rebasing ${branch} onto origin/main..."
            if ! git rebase origin/main; then
                echo "Rebase failed. Resolve conflicts and continue, or abort the rebase, before retrying." >&2
                exit 1
            fi
        fi
    fi
fi

# Type checking is independent of whether a rebase target exists.
bash "${ROOT}/scripts/dev.sh" check types
echo "Pre-push checks passed. Full pytest runs in CI."
