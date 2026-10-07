# 验证记录

日期：2026-10-07；基线 `87520ea`；分支 `fix/step1-step6-acceptance-review`；Windows，Python 3.14.6（不代替 CI Python 3.12）。

## 修复前复现

新增等待前写入、内部 grant、命令异体重放与 checkpoint 跨 session 反例，修复前 `4 failed`；修复后通过。原 SC02 把授权拒绝仍记 succeeded，修正为 failed，并检查零业务派发。

## 收口结果

- Runner/durable/runtime/shared execution、平台 execution、回调与 SC05 相关回归：`713 passed, 26 skipped`。
- 加入完整 Runtime、分层、kernel purity、自足、feature inventory 与场景清单后的收口：`813 passed, 26 skipped, 1 failed`。失败为新增 GET 唤醒拒绝在 Windows 下出现 TCP reset；读取声明请求体后拒绝，完整 Runner 重测 `144 passed`。剩余通过范围未受此 HTTP 改动影响。
- PostgreSQL durable：`75 passed, 1 skipped`；专用 postgres:16 容器，测试库 `hecate_review`，跳过为另一方言条件用例。
- Alembic upgrade head/ORM drift：`1 passed`；同容器独立数据库 `hecate_drift_test`。专用容器验证后删除，未使用现有业务 PostgreSQL。
- SC01/SC02/SC03/SC06 非 editable wheel 真进程：最终并行 `14 passed, 1 failed`，失败在 uv wheel build 阶段，无可用 stderr；补构建诊断后串行复测该审批进程用例及完整 waiting 文件 `17 passed`。此前同安装范围曾 `15 passed`。未把构建失败算作验收成功。
- SC03 fixture 后续发现重启后子进程泄漏，只停原实例；已改为停止 stack 当前实例，仅清理该 review 下本轮子进程。fixture 修正后的 SC03 串行复测 `2 passed`，覆盖终态写/审批等待进程重启。
- ruff check / format check：通过；mypy `src/ packages/hecate-runner/src packages/hecate-durable/src`：`Success: no issues found in 654 source files`；git diff check 和 OpenSpec strict：通过。

## CI follow-up

GitHub Actions full-suite run passed `6478` tests, skipped `58`, and failed only `test_sc03_approval_wait_survives_kill_then_wakes_once`: immediately after the wake receipt, the new run was still `queued`. `RunnerInstance.wait_run()` returned on any status other than `running`, so it treated this valid dispatch transition as a final result. The harness now polls through both `queued` and `running`, then returns on a waiting or terminal state. Local execution of the exact CI command was unavailable in this Windows session because the attached venv points to a missing `C:\Python314\python.exe`; GitHub Actions remains the authoritative verification for this follow-up.

## 可复现命令

共享 venv 原 editable 指向旧 checkout，必须显式指向 review 源码。PowerShell：

```powershell
$env:PYTHONPATH = @('src', 'packages/hecate-runtime/src', 'packages/hecate-durable/src', 'packages/hecate-runner/src') -join ';'
python -m pytest packages/hecate-runner/tests packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_execution tests/test_channel/test_workflow_callback_api.py tests/scenarios/test_sc05_managed_reconnect.py tests/test_layering_domain.py tests/test_layering_entry_imports.py tests/test_runtime/test_kernel_purity.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_scripts/test_feature_inventory.py tests/scenarios/test_manifest_consistency.py -q -n 3
python -m pytest tests/scenarios/test_sc01_cold_start.py tests/scenarios/test_sc02_inventory_read.py tests/scenarios/test_sc03_local_approval_restart.py tests/scenarios/test_sc06_evidence_failure.py -q
python -m mypy src/ packages/hecate-runner/src packages/hecate-durable/src
ruff check src/hecate/ tests/ packages/hecate-runner/src packages/hecate-runner/tests packages/hecate-durable/src packages/hecate-durable/tests
ruff format --check src/ tests/ packages/hecate-runner/src packages/hecate-runner/tests packages/hecate-durable/src packages/hecate-durable/tests
openspec validate step1-step6-acceptance-hardening --strict --no-interactive
```

PostgreSQL 用例需将 `DURABLE_TEST_POSTGRES_URL` 指向单独审查数据库；migration 用例使用 `DRIFT_ADMIN_URL` 与 `DRIFT_DATABASE_URL`，不能指向业务实例。本次没有运行全部应用测试，没有供应商模型或真实企业系统认证。
