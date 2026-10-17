# Verification

验证在 `docs/openspec-risk-levels` worktree 完成。

| Command | Result |
| --- | --- |
| `openspec schema validate lightweight --verbose` | Passed; schema YAML, templates, and dependency graph are valid. |
| `openspec validate openspec-risk-tiers --strict` | Passed; `skip_specs: true` is accepted for this policy/tooling-only change. |
| Temporary `lightweight-smoke`: `openspec validate lightweight-smoke --strict` | Passed with the generated `skip_specs: true`; temporary change directory was removed after validation. |
| `uv run --no-project --python 3.12 .opencode/skills/hecate-docs-sync/scripts/sync_specs.py --audit-readme --audit-offline` | Passed: 0 errors, 0 warnings, 21 OK; network links were skipped in offline mode. |
| `Resolve-Path docs/how-to/../../AGENTS.md` | Passed; documentation reference resolves inside the worktree. |
| `git diff --check` | Passed. |

The workflow-specific online Markdown link action was not run locally; CI runs it on changed Markdown files. No Python behavior or CI gate changed, so no product tests were applicable. The main checkout remains unchanged.
