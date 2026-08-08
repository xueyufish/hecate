---
name: hecate-docs-sync
description: Audit README.md for drift against `pyproject.toml`, `LICENSE`, shields.io badges, and GitHub links. Read-only — never modifies any file. Use in CI to catch stale README before PR merge, or before releases to confirm docs match the project. Triggers on "audit readme", "check readme", "hecate-docs-sync", "/docs-sync", "/audit-readme".
license: MIT
compatibility: Python 3.12+ recommended. No external dependencies (stdlib only).
metadata:
  author: Hecate Team
  version: "0.4.0"
  status: MVP (read-only, single mode)
---

# Hecate README Audit (Read-Only MVP)

Verifies `README.md` against `pyproject.toml`, `LICENSE`, shields.io badges, and
GitHub links, surfacing drift. Prints findings to stdout only — never modifies
any file.

## ⚠️ Scope and Constraints

| Path | Mode | Reason |
|------|------|--------|
| `openspec/specs/**` | **NEVER TOUCHED** | Owned by `/opsx-*` skills. This skill does not interfere. |
| `openspec/changes/**` | **NEVER TOUCHED** | Same. |
| `docs/**` | **NEVER WRITTEN** | Public-facing docs are human-authored. |
| `README.md` | **NEVER WRITTEN** | The audit reports drift but never modifies the file. |
| `pyproject.toml`, `LICENSE` | **READ-ONLY** | Used as ground truth. |
| `src/**` | **NEVER TOUCHED** | Implementation lives there. |

## Usage

```bash
python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --audit-readme [options]
```

| Flag | Description |
|------|-------------|
| `--audit-readme` | Required. Run the README audit. |
| `--audit-offline` | Skip network checks (badges and GitHub links). Useful in CI or offline environments. |
| `--json` | Emit machine-readable JSON for CI gating. |

## What It Checks

Five checks across two categories — **network** and **offline**:

| # | Check | Source | Network? |
|---|-------|--------|----------|
| 1 | **Badge URL validity** | shields.io URLs in README | Yes (skip with `--audit-offline`) |
| 2 | **GitHub link validity** | Non-badge `github.com/*` URLs in README | Yes (skip with `--audit-offline`) |
| 3 | **Python version match** | `pyproject.toml` `requires-python` vs README mention | No |
| 4 | **License match** | `LICENSE` first line vs README badge / mention | No |
| 5 | **Install commands sanity** | `pyproject.toml [project.scripts]` entries vs README mentions + `docker-compose.yml` presence | No |

Each finding is reported with severity `ok`, `warn`, `error`, or `skip`. The audit
does not write to any file; it only reports.

## Examples

```bash
# Full audit (with network checks)
python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --audit-readme

# Offline audit (CI / no internet)
python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --audit-readme --audit-offline

# JSON output for CI gating
python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --audit-readme --json \
  | jq '[.findings[] | select(.severity=="error")]'
```

## Sample Output

```
================================================================
Hecate README Audit Report
================================================================

## Summary
  Checks:  5 categories · 12 findings (0 error, 1 warn, 11 ok)

## Badges (shields.io URLs) (3)
  [OK   ]  https://img.shields.io/badge/License-MIT-yellow.svg    HTTP 200
  [OK   ]  https://img.shields.io/badge/python-3.12+-blue.svg      HTTP 200
  [OK   ]  https://img.shields.io/badge/status-alpha-orange         HTTP 200

## Install Commands (2)
  [WARN ]  console_scripts: pyproject.toml declares ['hecate', 'hecate-migrate']
          but README does not mention: ['hecate-migrate']
  [OK   ]  docker-compose.yml present & referenced in README
```

## End-to-End Workflow

The skill participates in a **human-in-the-loop** feedback cycle. It does NOT
auto-fix drift — it only reports. Closing the loop is a human action.

```
                         ┌──────────────────────────────────────┐
                         │  Edit README.md (or pyproject.toml  │
                         │  / LICENSE) by hand based on audit  │
                         └─────────────────┬────────────────────┘
                                           │ git push
                                           ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  CI: .github/workflows/docs-check.yml                           │
   │  ├─ skill --audit-readme --audit-offline   (semantic drift)     │
   │  └─ step-security/markdown-link-check       (dead URLs)         │
   └──────────────────────────────┬─────────────────────────────────┘
                                  │ findings
                                  ▼
                       ┌───────────────────────┐
                       │  Severity mapping:     │
                       │   ok     → silent ✅   │
                       │   warn   → comment     │
                       │   error  → red ✗       │
                       └───────────┬───────────┘
                                   │
                                   └──── back to the top ────┘
```

Concretely, on every push that touches `README.md`, `docs/`, this skill, or the workflow file:

1. **CI triggers `docs-check`**.
2. The audit runs twice — once offline (semantic drift vs `pyproject.toml` / `LICENSE`, fast, deterministic) and once online (badges and GitHub links, slower, may flake).
3. CI surfaces findings:
   - `ok` — no comment, no failure.
   - `warn` — comment summarising drift; PR can still merge.
   - `error` — workflow fails; PR cannot merge until the drift is fixed.
4. **You read the audit output** and edit `README.md`, `pyproject.toml`, or `LICENSE` by hand.
5. Push the fix; CI re-runs. The loop closes when the audit returns `0 error`.

What the skill **never** does (by design):

- It does not auto-edit `README.md`.
- It does not open pull requests.
- It does not post PR comments yet — wiring that is tracked under [Future Extensions](#future-extensions-not-mvp).
- It does not modify `pyproject.toml` or `LICENSE`.

Those remain human actions, or future skill extensions.

## When to Invoke

- Before committing README changes → catch dead badges or broken GitHub links.
- Before opening a release → verify README still matches `pyproject.toml` and `LICENSE`.
- In CI on every PR → run with `--audit-offline --json` and fail on `severity == "error"`.

## When NOT to Invoke

- To create or modify any docs file — this skill is read-only.
- To fix README drift — edit README by hand based on audit findings.

## CI Integration

The audit mode is designed to run in CI. A recommended workflow lives at `.github/workflows/docs-check.yml` and runs two complementary checks on every PR:

```yaml
name: docs-check
on:
  pull_request:
    branches: [main]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Audit README (semantic)
        run: |
          python .opencode/skills/hecate-docs-sync/scripts/sync_specs.py \
            --audit-readme --audit-offline
      - name: Check markdown links
        uses: step-security/github-action-markdown-link-check@v1
        with: { use-quiet-mode: "yes" }
```

The audit catches semantic drift (README vs `pyproject.toml` / `LICENSE`); the link-checker catches dead URLs. Together they cover the two main classes of README rot.

## Future Extensions (NOT MVP)

| Direction | Purpose |
|-----------|---------|
| Spec linting | Catch common anti-patterns (vague SHALL statements, missing WHEN/THEN). |
| Symbol drift | Scan `src/hecate/` for function/class names referenced in README but not in code. |
| Changelog generation | Read `openspec/changes/archive/` and emit a release-notes draft to paste into `docs/about/changelog.md`. |

All would remain **stdout-only** — no file writes under `docs/` or `README.md`.