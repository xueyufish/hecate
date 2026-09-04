## 1. Baseline

- [ ] 1.1 Verify branch `opsx/wave2-routers-relocate` HEAD = `c1f118e` (archive ride-along) on top of `b1a5f03` (#121); working tree clean
- [ ] 1.2 `grep -rn "hecate\.api\.management" src/ tests/` → confirm exactly 5 router files + main.py + wiring references (the known set)
- [ ] 1.3 Capture green baseline: ruff / format / mypy / targeted guard trio (layering_domain + runtime/core self-sufficiency)

## 2. Free moves (zero code change)

- [ ] 2.1 `git mv src/hecate/api/management/traces.py src/hecate/ops/api/traces.py`
- [ ] 2.2 Verify traces.py has no module-level business import (AST check) and guard trio still green

## 3. budget → enterprise + guarded mount

- [ ] 3.1 `git mv src/hecate/api/management/budget.py src/hecate/enterprise/api/budget.py`
- [ ] 3.2 main.py: remove unconditional `from hecate.api.management.budget import router as budget_router` + `app.include_router(budget_router, ...)`; re-add as try/except ImportError guarded mount (auth precedent, lines 386-393)
- [ ] 3.3 Keep budget.py's internal lazy import + 503 branch (defense-in-depth, per design D3)

## 4. sessions cluster → studio (B decision)

- [ ] 4.1 `git mv src/hecate/runtime/api/sessions.py src/hecate/studio/api/sessions.py`
- [ ] 4.2 sessions.py: convert `from hecate.runtime.eventstore import ...` and `from hecate.runtime.session_state import ...` to function-level lazy imports
- [ ] 4.3 `git mv src/hecate/api/management/replay.py src/hecate/studio/api/replay.py`
- [ ] 4.4 replay.py: lazy-ify `runtime.eventstore` + `runtime.replay.logfold` (studio.replay.* stay module-level — same domain)
- [ ] 4.5 `git mv src/hecate/api/management/conversations.py src/hecate/studio/api/conversations.py`
- [ ] 4.6 conversations.py: lazy-ify `runtime.eventstore` (the ops.ops_center.conversation_messages import is resolved by task 5.x)
- [ ] 4.7 `git mv src/hecate/api/management/collaboration_patterns.py src/hecate/studio/api/collaboration_patterns.py`
- [ ] 4.8 collaboration_patterns.py: lazy-ify `runtime.types` (1 line, per design D5)

## 5. conversation_messages helper rehome

- [ ] 5.1 Grep all consumers of `ops.ops_center.conversation_messages` across src/ + tests/
- [ ] 5.2 If conversations router is the only routing consumer: `git mv src/hecate/ops/ops_center/conversation_messages.py src/hecate/studio/conversations_messages.py` (or studio/api/ sibling — match repo convention) and update imports; keep ops ops_center tests green
- [ ] 5.3 If ops_center itself consumes it internally: leave in ops, conversations router lazy-imports it (already done in 4.6) — record the deviation in this file's task note

## 6. main.py rewrite

- [ ] 6.1 Rewrite 5 deferred import lines: budget (→ guarded mount, done in 3.2), collaboration_patterns / conversations / replay / traces → `from hecate.<domain>.api.<x> import router as ...`
- [ ] 6.2 Verify zero `from hecate.api.` references remain in main.py
- [ ] 6.3 `python -c "import hecate.main"` smoke OK

## 7. Tests + consumers path updates

- [ ] 7.1 Grep tests/ + src/ for `hecate.api.management.{budget,collaboration_patterns,conversations,replay,traces}` and `hecate.runtime.api.sessions` → sed to new paths
- [ ] 7.2 Grep for `ops.ops_center.conversation_messages` consumers per task 5.1 outcome
- [ ] 7.3 Verify zero stale references (grep clean)

## 8. Verification

- [ ] 8.1 ruff check + ruff format clean
- [ ] 8.2 mypy src/ clean
- [ ] 8.3 Full pytest → expect ≈4092+ passed / 0 failed
- [ ] 8.4 Guard trio (test_layering_domain + test_runtime_self_sufficiency + test_core_self_sufficiency) green
- [ ] 8.5 `ls src/hecate/api/` → directory gone (or only __init__ remnants removed)
- [ ] 8.6 pre-commit hook passes on commit

## 9. Docs sync

- [ ] 9.1 research doc §1.4 主包行: delete "遗留 5 个跨域 router" note
- [ ] 9.2 research doc §1.1 分歧注 ⑧: delete (promise fully fulfilled)
- [ ] 9.3 research doc §3.7 工期表: append this PR number after merge
- [ ] 9.4 Grep git-tracked docs for stale `api/management/` references → fix (research doc exempt from git but update anyway)

## 10. Commit & PR

- [ ] 10.1 Single commit `refactor(domain): relocate wave-2 routers — api/ layer disappears` with body covering: B decision, D2 framework correction, budget mount change (core-only behavior note: 404 instead of 503), conversation_messages outcome
- [ ] 10.2 Push (user approval per AGENTS.md)
- [ ] 10.3 Open PR; recommend Squash and merge; PR body notes the archive-commit ride-along at branch base (c1f118e)
- [ ] 10.4 After merge: backfill PR number into research doc §3.7; `openspec archive wave2-routers-relocate`; delete local branch