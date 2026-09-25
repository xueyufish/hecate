#!/bin/bash
# commit-msg hook: validate the Conventional Commits format via commitizen.
# The message file path is passed by git as $1.
#
# Install (once per clone; worktrees share .git/hooks/):
#   cp scripts/commit-msg.sh .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg

set -e

MSG_FILE="$1"
if [ -z "$MSG_FILE" ] || [ ! -f "$MSG_FILE" ]; then
    echo "❌ commit-msg hook: message file '$MSG_FILE' missing or not provided."
    exit 1
fi

VENV_DIR="$(git rev-parse --show-toplevel)/.venv"
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
elif [ -f "$VENV_DIR/Scripts/activate" ]; then
    source "$VENV_DIR/Scripts/activate"
else
    echo "❌ No .venv found at $VENV_DIR. Bootstrap it first (see .envrc header / AGENTS.md)."
    exit 1
fi

if ! python -m commitizen check --commit-msg-file "$MSG_FILE"; then
    echo "❌ Commit message does not follow Conventional Commits."
    echo "   Format: <type>(<scope>): <subject> — e.g. 'fix(runtime): handle empty queue'"
    exit 1
fi
