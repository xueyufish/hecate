# syntax=docker/dockerfile:1.7
# Hecate browser sandbox image — hosts Playwright + chromium-headless-shell
# for the browser_* builtin tools (6.27). Separate from the generic
# hecate-sandbox image used by execute_code so deployments that opt out of
# browser tools never pay the browser footprint.
#
# Size budget (CI-enforced at <=700MB):
# - `--only-shell` installs just the headless shell; full Chromium (~450MB)
#   is never downloaded (driver is strictly headless).
# - System libraries are hand-picked with --no-install-recommends instead of
#   `playwright install-deps`, whose Recommends pulls ~280MB of dead weight:
#   libllvm19+mesa via libgbm1 (GL software-raster stack, unused because the
#   driver passes --disable-gpu and headless shell ships SwiftShader),
#   CJK/emoji/freefont collections, libz3. fonts-liberation covers text.
# - PLAYWRIGHT_BROWSERS_PATH moves binaries out of /root so the non-root
#   `sandbox` user can execute them at runtime.

ARG PYTHON_VERSION=3.12
# Pinned to bookworm: on trixie, libgbm1 hard-depends on mesa-libgallium ->
# libllvm19 -> libz3 (~195MB of GL software-raster stack that a --disable-gpu
# headless shell never uses). Bookworm's libgbm1 needs only libdrm2.
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

# curl stays in the runtime image: BrowserSessionManager health checks and
# tool calls run `docker exec <id> curl http://127.0.0.1:8080/...`.
#
# System libraries are hand-picked (--no-install-recommends) instead of
# `playwright install-deps`, whose Recommends pulls ~280MB of dead weight for
# a headless-only image: libllvm19+mesa (GL stack, 167MB), CJK/emoji/freefont
# collections (~85MB), libz3 (27MB). fonts-liberation covers text rendering.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fontconfig \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libnss3 \
        libnspr4 \
        libpango-1.0-0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sandbox
COPY entrypoint.py /opt/sandbox/entrypoint.py

# Playwright floor is 1.49: `--only-shell` was introduced there. Floor kept
# in lockstep with pyproject.toml's [tools] constraint upper bound (<2.0).
RUN pip install --no-cache-dir "playwright>=1.49,<2.0" \
    && playwright install chromium --only-shell \
    && chmod -R a+rX /opt/ms-playwright \
    && rm -rf /tmp/* /var/tmp/* \
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

ENTRYPOINT ["python", "/opt/sandbox/entrypoint.py"]
CMD []
