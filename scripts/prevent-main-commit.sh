#!/usr/bin/env bash
# Prevent commits directly to the protected main branch.
#
# Called by the tracked pre-commit hook before any development checks.

set -euo pipefail

branch="$(git rev-parse --abbrev-ref HEAD)"

if [[ "${branch}" == "main" ]]; then
    cat >&2 <<'EOF'
ERROR: refusing to commit directly to 'main'.

  'main' is a protected branch. All changes go through pull requests.

Recovery recipe:
  git stash push -u -m "<topic>"
  git checkout -b docs/<topic>   # or feat/<topic> / fix/<topic> / chore/<topic>
  git stash pop
  git commit -m "..."

EOF
    exit 1
fi