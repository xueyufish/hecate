# hecate-sandbox

Hecate's sandbox / browser / environment domain, extracted from the
core package as part of the package-split plan (PR4c).

## Contents

- **sandbox** — Docker-isolated command execution (`SandboxExecutor`,
  `SandboxPool`, `SandboxConfig` / `SandboxResult`).
- **environment** — agent runtime filesystem abstraction (`AgentEnvironment`
  ABC, `LocalEnvironment`, `DockerEnvironment`, `EnvironmentManager`).
- **browser** — Playwright/headless-Chromium session manager
  (`BrowserSession`, `BrowserSessionManager`); uses the
  `hecate-browser-sandbox` Docker image (no SDK dependency in the
  default install — the browser extras bring `playwright` for
  out-of-Docker use).

## Relationship to core

`hecate-sandbox` is a **required** dependency of the core `hecate`
package: tool execution (the `execute_code` builtin and the
`browser_*` tools) is a platform capability, not an optional extra.
The engine layer is unaffected — `engine/offloader.py` uses
`AgentEnvironment` only as a `TYPE_CHECKING` reference, so the
engine self-sufficiency guard (`test_engine_self_sufficiency`) is
preserved by extending its block list.

`services/plugin/` and `services/plugin/service.py` remain in core
(this PR lifts the top-level `plugin/` to `core/plugin/`, but the
plugin **service** that orchestrates the platform stays in core too).
`services/tool/registry.py` and `services/tool/builtin.py` likewise
stay in core — the tool *definitions* are core; their *execution*
path (e.g. `execute_code` calling into the sandbox) is wired through
this package at runtime.

## Install

```bash
# As part of the uv workspace (recommended for development)
uv sync --package hecate --package hecate-sandbox --extra dev --prerelease=allow

# For browser automation via Playwright (instead of the default
# hecate-browser-sandbox Docker image)
pip install 'hecate-sandbox[browser]'
```
