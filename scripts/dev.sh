#!/usr/bin/env bash
# Select this worktree's Python; uv bootstraps the standard-library CLI if absent.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for python in "${ROOT}/.venv/bin/python" "${ROOT}/.venv/Scripts/python.exe"; do
    if [[ -x "${python}" ]]; then
        exec "${python}" "${ROOT}/scripts/dev.py" "$@"
    fi
done
if [[ "${1:-}" == "doctor" ]]; then
    echo "dev: missing worktree interpreter; run: bash scripts/dev.sh sync" >&2
    exit 1
fi
exec uv run --no-project --python 3.12 "${ROOT}/scripts/dev.py" "$@"
