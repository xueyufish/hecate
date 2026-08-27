#!/usr/bin/env bash
# Hecate one-liner installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/xueyufish/hecate/main/install.sh | bash
#
# Behavior:
#   - Detects whether we are already inside a Hecate checkout (pyproject.toml with
#     name = "hecate") and reuses it, OR clones the repo into $HECATE_INSTALL_DIR
#     (default ~/.hecate-src).
#   - Ensures `uv` is on PATH; installs it via astral.sh if missing.
#   - Execs `uv run python scripts/install.py` which does the interactive setup:
#     copy .env, prompt for an LLM provider key, start docker compose, run
#     alembic, run hecate preflight.
#
# Environment overrides:
#   HECATE_INSTALL_DIR  target directory to install into (default ~/.hecate-src)
#   HECATE_REPO         git URL to clone (default https://github.com/xueyufish/hecate.git)
#   HECATE_BRANCH       branch to clone (default main)
#   HECATE_NONINTERACTIVE=1  skip interactive prompts (use placeholders)
#
# Windows: not supported here. Use install.ps1 (TBD).
set -euo pipefail

# Step 1: announce
echo "==> Hecate installer"

# Step 2: require git
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git not found. Install git and retry." >&2
    exit 1
fi

# Step 3: ensure uv
if ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # astral install script writes to ~/.local/bin and ~/.cargo/bin
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        echo "ERROR: uv install failed. Install uv manually (https://astral.sh/uv) and retry." >&2
        exit 1
    fi
fi

# Step 4: locate or clone the repo
HECATE_INSTALL_DIR="${HECATE_INSTALL_DIR:-$HOME/.hecate-src}"
HECATE_REPO="${HECATE_REPO:-https://github.com/xueyufish/hecate.git}"
HECATE_BRANCH="${HECATE_BRANCH:-main}"

if [ -f "pyproject.toml" ] && grep -q '^name = "hecate"' pyproject.toml 2>/dev/null; then
    HECATE_REPO_DIR="$(pwd)"
    echo "==> Using existing Hecate checkout at $HECATE_REPO_DIR"
elif [ -d "$HECATE_INSTALL_DIR" ]; then
    HECATE_REPO_DIR="$HECATE_INSTALL_DIR"
    echo "==> Using existing Hecate install at $HECATE_REPO_DIR"
else
    echo "==> Cloning Hecate ($HECATE_BRANCH) to $HECATE_INSTALL_DIR"
    git clone --depth=1 --branch="$HECATE_BRANCH" "$HECATE_REPO" "$HECATE_INSTALL_DIR"
    HECATE_REPO_DIR="$HECATE_INSTALL_DIR"
fi

# Step 5: dispatch to Python installer.
# fastmcp 4.x is currently beta-only; allow pre-releases so the outer `uv run`
# below can resolve dependencies for the project. install.py also sets this
# for its own subprocess calls.
export UV_PRERELEASE=allow
cd "$HECATE_REPO_DIR"
exec uv run python install.py "$@"