## 1. Setup & baseline

- [ ] 1.1 Confirm branch `opsx/api-management-relocate` clean off main @ `ec5cfdf` (current HEAD); verify zero uncommitted changes
- [ ] 1.2 Capture current test baseline: `python -m pytest tests/ -q --tb=short` → expect 4089 passed, 32 skipped
- [ ] 1.3 Capture current ruff + mypy + smart-pytest.sh ops-scoped baseline as green reference

## 2. Mechanical router relocate — `core/` domain (2 files + middleware)

- [ ] 2.1 `git mv src/hecate/api/management/feature_flags.py src/hecate/core/api/feature_flags.py`
- [ ] 2.2 `git mv src/hecate/api/management/i18n.py src/hecate/core/api/i18n.py`
- [ ] 2.3 Create `src/hecate/core/middleware/audit.py` from `src/hecate/api/middleware.py` (extract `AuditMiddleware` class only)
- [ ] 2.4 Delete `src/hecate/api/middleware.py`
- [ ] 2.5 Verify no test files import `hecate.api.middleware` (grep); if any, sed-replace to `hecate.core.middleware.audit`

## 3. Mechanical router relocate — `runtime/` domain (2 files)

- [ ] 3.1 `git mv src/hecate/api/management/sessions.py src/hecate/runtime/api/sessions.py`
- [ ] 3.2 `git mv src/hecate/api/management/hooks.py src/hecate/runtime/api/hooks.py`

## 4. Mechanical router relocate — `tools/` domain (6 files)

- [ ] 4.1 `git mv src/hecate/api/management/mcp.py src/hecate/tools/api/mcp.py`
- [ ] 4.2 `git mv src/hecate/api/management/skill_registry.py src/hecate/tools/api/skill_registry.py`
- [ ] 4.3 `git mv src/hecate/api/management/skills.py src/hecate/tools/api/skills.py`
- [ ] 4.4 `git mv src/hecate/api/management/tool_cache.py src/hecate/tools/api/tool_cache.py`
- [ ] 4.5 `git mv src/hecate/api/management/tools.py src/hecate/tools/api/tools.py`
- [ ] 4.6 `git mv src/hecate/api/management/tool_policies.py src/hecate/tools/api/tool_policies.py`

## 5. Mechanical router relocate — `channel/` domain (1 file)

- [ ] 5.1 `git mv src/hecate/api/management/a2a.py src/hecate/channel/api/a2a.py`

## 6. Mechanical router relocate — `ops/` domain (16 files, biggest)

- [ ] 6.1 `git mv src/hecate/api/audit.py src/hecate/ops/api/audit.py`
- [ ] 6.2 `git mv src/hecate/api/evaluation.py src/hecate/ops/api/evaluation.py`
- [ ] 6.3 `git mv src/hecate/api/schedules.py src/hecate/ops/api/schedules.py`
- [ ] 6.4 `git mv src/hecate/api/security_findings.py src/hecate/ops/api/security_findings.py`
- [ ] 6.5 `git mv src/hecate/api/tool_decisions.py src/hecate/ops/api/tool_decisions.py`
- [ ] 6.6 `git mv src/hecate/api/management/dlp.py src/hecate/ops/api/dlp.py`
- [ ] 6.7 `git mv src/hecate/api/management/costs.py src/hecate/ops/api/costs.py`
- [ ] 6.8 `git mv src/hecate/api/management/model_pricing.py src/hecate/ops/api/model_pricing.py`
- [ ] 6.9 `git mv src/hecate/api/management/quotas.py src/hecate/ops/api/quotas.py`
- [ ] 6.10 `git mv src/hecate/api/management/preflight.py src/hecate/ops/api/preflight.py`
- [ ] 6.11 `git mv src/hecate/api/management/agent_health.py src/hecate/ops/api/agent_health.py`
- [ ] 6.12 `git mv src/hecate/api/management/conversation_analytics.py src/hecate/ops/api/conversation_analytics.py`
- [ ] 6.13 `git mv src/hecate/api/management/tool_analytics.py src/hecate/ops/api/tool_analytics.py`
- [ ] 6.14 `git mv src/hecate/api/management/ops_center_overview.py src/hecate/ops/api/ops_center_overview.py`
- [ ] 6.15 `git mv src/hecate/api/system/backup.py src/hecate/ops/api/backup.py` (note: also remove the now-empty `api/system/` directory)

## 7. Mechanical router relocate — `studio/` domain (7 files)

- [ ] 7.1 `git mv src/hecate/api/management/agents.py src/hecate/studio/api/agents.py`
- [ ] 7.2 `git mv src/hecate/api/management/agent_templates.py src/hecate/studio/api/agent_templates.py`
- [ ] 7.3 `git mv src/hecate/api/management/workflows.py src/hecate/studio/api/workflows.py`
- [ ] 7.4 `git mv src/hecate/api/management/prompts.py src/hecate/studio/api/prompts.py`
- [ ] 7.5 `git mv src/hecate/api/management/plugins.py src/hecate/studio/api/plugins.py`
- [ ] 7.6 `git mv src/hecate/api/management/orchestration_templates.py src/hecate/studio/api/orchestration_templates.py`

## 8. Mechanical router relocate — `enterprise/` domain (3 files)

- [ ] 8.1 `git mv src/hecate/api/management/api_keys.py src/hecate/enterprise/api/api_keys.py`
- [ ] 8.2 `git mv src/hecate/api/auth.py src/hecate/enterprise/api/auth.py`
- [ ] 8.3 `git mv src/hecate/api/management/model_providers.py src/hecate/enterprise/api/model_providers.py`

## 9. `main.py` rewrite

- [ ] 9.1 Replace ~40 lines of `from hecate.api.X import router as X_router` with `from hecate.<domain>.api.X import router as X_router`, grouped per domain with `# --- <domain> ---` separator
- [ ] 9.2 Update `app.add_middleware(AuditMiddleware)` (line 189) to import from `hecate.core.middleware.audit`
- [ ] 9.3 Verify all imports resolve via `python -c "import hecate.main"`
- [ ] 9.4 Verify no `from hecate.api.` references remain in `main.py` (except `auth` if kept — see open question D8)

## 10. Test path updates

- [ ] 10.1 `grep -lr "hecate\.api\.management\|hecate\.api\.middleware\|hecate\.api\." tests/` → enumerate all referencing test files
- [ ] 10.2 Sed-replace each `from hecate.api.management.X` import to `from hecate.<domain>.api.X`
- [ ] 10.3 Sed-replace each `hecate.api.middleware` reference to `hecate.core.middleware.audit`
- [ ] 10.4 Sed-replace each `patch("hecate.api.management.X")` and `patch.object` string to new path
- [ ] 10.5 Verify zero stale `hecate.api.` references in `tests/`

## 11. Verification

- [ ] 11.1 `ruff check src/hecate/ tests/` clean
- [ ] 11.2 `ruff format --check src/hecate/ tests/` clean (run `ruff format` if not)
- [ ] 11.3 `mypy src/` clean
- [ ] 11.4 Full `python -m pytest tests/ -q` → expect ≈4089 passed (no regression)
- [ ] 11.5 `python -m pytest tests/test_layering_domain.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_build/test_core_self_sufficiency.py -q` → all green
- [ ] 11.6 `python -c "import hecate.main; import hecate.ops.scheduling.executors"` → smoke OK
- [ ] 11.7 pre-commit hook runs cleanly on commit attempt

## 12. Documentation sync

- [ ] 12.1 Edit `docs/research/industry-architecture-comparison.md` §1.4 主包行 — remove "遗留：顶层 api/management/* 41 个管理路由待归域（note" 注记
- [ ] 12.2 Edit `docs/research/industry-architecture-comparison.md` §1.1 分歧注 ⑧ — remove (now obsolete after PR lands)
- [ ] 12.3 Edit `docs/research/industry-architecture-comparison.md` §3.5 Phase R table — update to reflect PR #121 (this change)
- [ ] 12.4 Verify no stale `src/hecate/api/management/` paths in any committed doc (research doc + git-tracked docs)

## 13. Commit & PR

- [ ] 13.1 Single `git commit -m "refactor(domain): relocate api/management routers per Phase R §1.1"` with detailed body (mirrors PR #120 commit message style)
- [ ] 13.2 `git push --set-upstream origin opsx/api-management-relocate` (after user approval per AGENTS.md Git rule)
- [ ] 13.3 Open PR via GitHub UI; recommend "Squash and merge" (per AGENTS.md `Merge commits are disabled at the repo level`)
- [ ] 13.4 Update `docs/research/...` §3.7 schedule table Phase R row to include this PR number

## Out of scope (future change)

- 3 个棘手 router (`model_providers` / `budget` / `sessions` / `replay` / `conversations`) composition pattern — independent OpenSpec change
- `auth` router's deep coupling with `hecate_enterprise.services.auth.service` — note in design.md D4