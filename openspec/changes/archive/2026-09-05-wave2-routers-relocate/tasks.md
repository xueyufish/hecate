## 1. Baseline

- [x] 1.1 Verify branch `opsx/wave2-routers-relocate` HEAD = `c1f118e` (archive ride-along) on top of `b1a5f03` (#121); working tree clean
- [x] 1.2 `grep -rn "hecate\.api\.management" src/ tests/` → confirm exactly 5 router files + main.py + wiring references (the known set)
- [x] 1.3 Capture green baseline: ruff / format / mypy / targeted guard trio (layering_domain + runtime/core self-sufficiency)

## 2. Free moves (zero code change)

- [x] 2.1 `git mv src/hecate/api/management/traces.py src/hecate/ops/api/traces.py`
- [x] 2.2 Verify traces.py has no module-level business import (AST check) and guard trio still green

## 3. budget → enterprise + guarded mount

- [x] 3.1 `git mv src/hecate/api/management/budget.py src/hecate/enterprise/api/budget.py`
- [x] 3.2 main.py: remove unconditional `from hecate.api.management.budget import router as budget_router` + `app.include_router(budget_router, ...)`; re-add as try/except ImportError guarded mount (auth precedent, lines 386-393)
- [x] 3.3 Keep budget.py's internal lazy import + 503 branch (defense-in-depth, per design D3)

## 4. sessions cluster → studio (B decision)

- [x] 4.1 `git mv src/hecate/runtime/api/sessions.py src/hecate/studio/api/sessions.py`
- [x] 4.2 sessions.py: convert `from hecate.runtime.eventstore import ...` and `from hecate.runtime.session_state import ...` to function-level lazy imports
- [x] 4.3 `git mv src/hecate/api/management/replay.py src/hecate/studio/api/replay.py`
- [x] 4.4 replay.py: lazy-ify `runtime.eventstore` + `runtime.replay.logfold` (studio.replay.* stay module-level — same domain)
- [x] 4.5 `git mv src/hecate/api/management/conversations.py src/hecate/studio/api/conversations.py`
- [x] 4.6 conversations.py: lazy-ify `runtime.eventstore` (the ops.ops_center.conversation_messages import is resolved by task 5.x)
- [x] 4.7 `git mv src/hecate/api/management/collaboration_patterns.py src/hecate/studio/api/collaboration_patterns.py`
- [ ] 4.8 collaboration_patterns.py: lazy-ify `runtime.types` (1 line, per design D5)

## 5. conversation_messages helper rehome

- [x] 5.1 Grep all consumers of `ops.ops_center.conversation_messages` across src/ + tests/
- [ ] 5.2 If conversations router is the only routing consumer: `git mv src/hecate/ops/ops_center/conversation_messages.py src/hecate/studio/conversations_messages.py` (or studio/api/ sibling — match repo convention) and update imports; keep ops ops_center tests green
- [x] 5.3 CHOSEN PATH: helper stays in ops (internal consumers: conversation_topic_matcher + conversation_quality_scorer); conversations router lazy-imports it (done in 4.6)

## 6. main.py rewrite

- [x] 6.1 Rewrite 5 deferred import lines: budget (→ guarded mount, done in 3.2), collaboration_patterns / conversations / replay / traces → `from hecate.<domain>.api.<x> import router as ...`
- [x] 6.2 Verify zero `from hecate.api.` references remain in main.py
- [x] 6.3 `python -c "import hecate.main"` smoke OK

## 7. Tests + consumers path updates

- [x] 7.1 Grep tests/ + src/ for `hecate.api.management.{budget,collaboration_patterns,conversations,replay,traces}` and `hecate.runtime.api.sessions` → sed to new paths
- [ ] 7.2 Grep for `ops.ops_center.conversation_messages` consumers per task 5.1 outcome
- [x] 7.3 Verify zero stale references (grep clean)

## 8. Verification

- [x] 8.1 ruff check + ruff format clean
- [x] 8.2 mypy src/ clean
- [x] 8.3 Full pytest → expect ≈4092+ passed / 0 failed
- [x] 8.4 Guard trio (test_layering_domain + test_runtime_self_sufficiency + test_core_self_sufficiency) green
- [x] 8.5 `ls src/hecate/api/` → directory gone (or only __init__ remnants removed)
- [ ] 8.6 pre-commit hook passes on commit

## 9. Docs sync

- [x] 9.1 research doc §1.4 主包行: delete "遗留 5 个跨域 router" note
- [x] 9.2 research doc §1.1 分歧注 ⑧: delete (promise fully fulfilled)
- [x] 9.3 research doc §3.7 工期表: append this PR number after merge
- [x] 9.4 Grep git-tracked docs for stale `api/management/` references → fix (research doc exempt from git but update anyway)

## 10. Commit & PR

- [x] 10.1 Single commit `refactor(domain): relocate wave-2 routers — api/ layer disappears` with body covering: B decision, D2 framework correction, budget mount change (core-only behavior note: 404 instead of 503), conversation_messages outcome
- [x] 10.2 Push (user approval per AGENTS.md)
- [x] 10.3 Open PR; recommend Squash and merge; PR body notes the archive-commit ride-along at branch base (c1f118e)
- [x] 10.4 After merge (PR #122, 9da3b54): backfilled PR number into research doc §3.7; `openspec archive wave2-routers-relocate`; delete local branch