#!/usr/bin/env bash
# OpenSpec + worktree unified flow.
#
# This is the ONLY entry point for all OpenSpec-related work on the main repo.
# It wraps worktree-help.sh with OpenSpec-specific subcommands so the agent
# (and you) never need to run plain `git checkout -b` from the main checkout.
#
# Usage:
#   ./scripts/opsx-flow.sh start <name> [--tools <list>|--no-tools]
#                                       # create worktree + openspec init, no auto-launch
#   ./scripts/opsx-flow.sh launch <name> [--tool <name>] [--background]
#                                       # bind worktree to an AI tool (opencode by default)
#   ./scripts/opsx-flow.sh push  <name> # push worktree branch
#   ./scripts/opsx-flow.sh delete <name>
#                                       # remove worktree + branch after merge
#   ./scripts/opsx-flow.sh remove <name>
#                                       # remove worktree only, keep branch
#   ./scripts/opsx-flow.sh list         # list active worktrees
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
WT_SCRIPT="${REPO_ROOT}/scripts/worktree-help.sh"
[[ -f "${WT_SCRIPT}" ]] || { echo "worktree-help.sh not found"; exit 1; }

HECATE_WORKTREE_ROOT="${HECATE_WORKTREE_ROOT:-${REPO_ROOT}/../.worktrees}"

cmd="${1:-help}"
shift || true

case "${cmd}" in
    start|push|clean|list|delete|remove|launch)
        HECATE_WORKTREE_ROOT="${HECATE_WORKTREE_ROOT}" "${WT_SCRIPT}" "${cmd}" "$@"
        ;;
    help|*)
        cat <<EOF
Usage:
  $0 start <name> [--tools <list>|--no-tools]
                                  # create worktree + openspec init (no auto-launch)
  $0 launch <name> [--tool <name>] [--background]
                                  # bind worktree to an AI tool
  $0 push  <name>                 # push worktree branch to origin
  $0 delete <name>                # remove worktree + branch after merge
  $0 remove <name>                # remove worktree only, keep branch
  $0 list                         # list active worktrees

start --tools flags:
  --tools <list>   comma-separated list passed to 'openspec init --tools'
                   (default: opencode,zcode,codex)
  --no-tools       skip 'openspec init' entirely

launch --tool flags:
  --tool <name>    AI CLI to launch in the worktree (default: opencode)
                   common values: opencode, zcode, workbuddy, codex
  --background     launch detached; logs go to <wt>/.<tool>.log

All OpenSpec work happens inside a worktree at \$HECATE_WORKTREE_ROOT/<name>.
The main repo is never checked out for OpenSpec changes.

Typical workflow:
  $0 start my-change                          # creates worktree, inits openspec
  $0 launch my-change --tool codex            # bind to codex (foreground)
  # ... run /opsx-propose, /opsx-apply, etc. inside the worktree ...
  $0 push  my-change                          # pushes feat/my-change to origin
  $0 delete my-change                         # cleans up after merge
EOF
        ;;
esac
