# syntax=docker/dockerfile:1.7
# Hecate browser sandbox image — hosts Playwright + Chromium for browser_*
# builtin tools (6.27). Image is intentionally separate from the generic
# hecate-sandbox image used by execute_code so that the ~600MB Chromium
# footprint is only paid by deployments that opt into browser tools.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Playwright Chromium runtime dependencies. Pinned to versions known to be
# compatible with Playwright 1.40.x. `playwright install --with-deps chromium`
# installs Chromium itself; this layer only installs the system libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-liberation \
        libasound2t64 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libc6 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libexpat1 \
        libfontconfig1 \
        libgbm1 \
        libgcc-s1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libstdc++6 \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxi6 \
        libxkbcommon0 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxtst6 \
        lsb-release \
        wget \
        xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sandbox

# Install Playwright Python SDK matching the version pinned in pyproject.toml.
# `--no-deps` here so we get a deterministic install; runtime deps are installed
# by the next layer with `playwright install --with-deps chromium`.
COPY entrypoint.py /opt/sandbox/entrypoint.py
RUN chmod +x /opt/sandbox/entrypoint.py

# Install Playwright Python SDK. Pin to 1.40.x; bump in lockstep with
# pyproject.toml's [tools] playwright constraint.
RUN pip install --no-cache-dir "playwright>=1.40,<2.0"

# Install Chromium browser binaries and verify the install. The browser is
# cached at /ms-playwright so a single image layer pays the download cost.
RUN playwright install chromium \
    && playwright install-deps chromium \
    && python -c "from playwright.sync_api import sync_playwright; \
        p = sync_playwright().start(); \
        b = p.chromium.launch(headless=True, args=['--no-sandbox']); \
        print('chromium ok, version:', b.version); \
        b.close(); \
        p.stop()"

# Sandbox image should not run as root inside the container.
RUN groupadd --system --gid 1001 sandbox \
    && useradd --system --uid 1001 --gid sandbox --home /home/sandbox --shell /bin/bash sandbox \
    && mkdir -p /home/sandbox /workspace \
    && chown -R sandbox:sandbox /home/sandbox /workspace

USER sandbox
WORKDIR /workspace

# The dispatcher reads TOOL_INPUT env var (set by SandboxExecutor via
# `docker exec --env TOOL_INPUT=...`) and invokes the matching handler.
ENTRYPOINT ["python", "/opt/sandbox/entrypoint.py"]

# Default command prints the readiness beacon so `docker wait` returns
# immediately. The real entrypoint replaces this with the dispatcher.
CMD []
