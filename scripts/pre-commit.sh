#!/usr/bin/env bash
# Fast local checks. Install dispatchers with: bash scripts/dev.sh hooks install
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
bash "${ROOT}/scripts/prevent-main-commit.sh"
bash "${ROOT}/scripts/dev.sh" check lint
bash "${ROOT}/scripts/dev.sh" check format
