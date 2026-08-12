#!/usr/bin/env bash
# Prevent commits directly to the protected main branch.
#
# Install once per clone:
#   cp scripts/prevent-main-commit.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# Worktrees share hooks with the main repo, so installing in the main
# .git/hooks/ covers all worktrees.
#
# Bypass: git commit --no-verify (only for explicit edge cases such as
# release tagging from main, never for routine edits).

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

Bypass only for explicit edge cases (e.g. release tagging):
  git commit --no-verify -m "..."
EOF
    exit 1
fi