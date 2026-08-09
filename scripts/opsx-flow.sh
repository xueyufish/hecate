#!/usr/bin/env bash
# OpenSpec + worktree unified flow.
#
# This is the ONLY entry point for all OpenSpec-related work on the main repo.
# It wraps dev-worktree.sh with OpenSpec-specific subcommands so the agent
# (and you) never need to run plain `git checkout -b` from the main checkout.
#
# Usage:
#   ./scripts/opsx-flow.sh start <name>   # create worktree + start session
#   ./scripts/opsx-flow.sh push  <name>   # push worktree branch
#   ./scripts/opsx-flow.sh clean <name>   # cleanup
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
WT_SCRIPT="${REPO_ROOT}/scripts/dev-worktree.sh"
[[ -f "${WT_SCRIPT}" ]] || { echo "dev-worktree.sh not found"; exit 1; }

HECATE_WORKTREE_ROOT="${HECATE_WORKTREE_ROOT:-${REPO_ROOT}/../.worktrees}"

cmd="${1:-help}"
shift || true

case "${cmd}" in
    start|push|clean|list)
        HECATE_WORKTREE_ROOT="${HECATE_WORKTREE_ROOT}" "${WT_SCRIPT}" "${cmd}" "$@"
        ;;
    help|*)
        cat <<EOF
Usage:
  $0 start <name>        # create worktree + start opencode in it
  $0 push  <name>        # push worktree branch to origin
  $0 clean [<name>]      # remove worktree(s)

All OpenSpec work happens inside a worktree at \$HECATE_WORKTREE_ROOT/<name>.
The main repo is never checked out for OpenSpec changes.
EOF
        ;;
esac
