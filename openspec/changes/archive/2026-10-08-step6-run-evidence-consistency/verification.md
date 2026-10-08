# 验证记录

基线 `b73af85`；分支 `fix/step6-followup-review`；本地 Windows / Python 3.14.6。

## 复现与结果

- 首轮新增 HTTP/SQL 负例：`3 failed`，证据为多页只收到首页、缺口没有后续标记、等待事件流 terminal=true。
- 首次修正后 Runner/durable、平台 execution 和 workflow callback：`705 passed, 26 skipped`。
- 追加提交重放和重复取消负例：`3 failed`，成功/失败 Run 在重启后 received_at/key 改变，重复取消产生不同命令 ID。
- 修正后 Runner/durable、事件中继、domain/entry 分层、kernel purity 与 wheel 安装的 SC03 真进程：`228 passed, 1 skipped`，包含审批等待、kill/restart、原等待和新成功尝试分别查询、业务写一次。
- PostgreSQL/SQLite durable：最初 postgres extra 未安装，数据库用例 setup 报 ModuleNotFoundError，不作为测试通过；安装 pyproject 已声明的 `psycopg[binary]` 后 `79 passed, 1 skipped`，包括完整 Run/source 的 JSON 状态查询与缺口分页。
- 取消终态与 applied 同事务修正后，完整 Runner 最终回归：`148 passed`。实际阻塞业务写时重复取消，禁用事务后的单独 applied 更新，回执仍正确应用；完成后再次取消不改写原回执。
- 完整 `mypy src/ packages/`：`Success: no issues found in 892 source files`。
- ruff check / format：通过；OpenSpec strict validate：通过；git diff check：通过。

## 命令

从 review 工作树运行；为避免 editable 指向其他 checkout，明确设置源码路径。受限会话访问解释器/工具需使用已授权环境。

```powershell
$env:PYTHONPATH = @('src', 'packages/hecate-runtime/src', 'packages/hecate-durable/src', 'packages/hecate-runner/src') -join ';'
.\.venv\Scripts\python.exe -m pytest packages/hecate-runner/tests packages/hecate-durable/tests tests/test_execution tests/test_channel/test_workflow_callback_api.py -q -n 3 -p no:cacheprovider --timeout=120
.\.venv\Scripts\python.exe -m pytest packages/hecate-runner/tests packages/hecate-durable/tests tests/test_execution/test_event_relay_projection.py tests/test_layering_domain.py tests/test_layering_entry_imports.py tests/test_runtime/test_kernel_purity.py tests/scenarios/test_sc03_local_approval_restart.py -q -n 3 -p no:cacheprovider --timeout=180
.\.venv\Scripts\python.exe -m pytest packages/hecate-runner/tests -q -n 3 -p no:cacheprovider --timeout=120
.\.venv\Scripts\python.exe -m mypy src/ packages/
.\.venv\Scripts\python.exe -m ruff check src/hecate/ tests/ packages/hecate-runner/ packages/hecate-durable/
.\.venv\Scripts\python.exe -m ruff format --check src/ tests/ packages/hecate-runner/ packages/hecate-durable/
openspec validate step6-run-evidence-consistency --strict --no-interactive
git diff --check
```

PostgreSQL 使用本轮临时 `postgres:16` 容器 `hecate-step6-followup-pg` 与专用 `hecate_review` 数据库，在仅此数据库设置 `DURABLE_TEST_POSTGRES_URL` 后串行执行 durable 包测试。容器使用 --rm，验证后 docker stop 已清理。不触及其他数据库，未新增 migration。

## 边界

条件跳过不作认证。此结果不替代 CI Python 3.12，不包括全部应用测试、完整受管 wheel+真实平台 HTTP/PG 宿主组合、平台 native continuation、企业审批或供应商模型认证。继续保留 Step6a～6f 原关闭门槛。
