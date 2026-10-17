#!/usr/bin/env bash
# Conventional Commits validation through this worktree's interpreter.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
bash "${ROOT}/scripts/dev.sh" commit-msg "${1:?Commit message file is required}"
