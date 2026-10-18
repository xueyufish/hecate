# 验证记录 — step6-accepted-action-batch-recovery

本记录为复核修正后的实际结果，取代初版记录。GLM 原新增套件 SQLite 基线为 13 passed。新增回归首次为 **10 failed、9 passed**，复现不同决策存储替换参数、中途返回不同有效决策、存储故障误分类/记录消失丢失前缀，以及非法 step 身份先访问存储的问题。修正后相关测试全部通过。

## 共享检查

```text
.venv/Scripts/python.exe scripts/dev.py check all
ruff check: All checks passed!
ruff format --check: 1605 files already formatted
mypy: Success: no issues found in 954 source files
```

`openspec validate step6-accepted-action-batch-recovery --strict` 和 `git diff --check` 均通过。另检查未跟踪的新代码与 artifacts 尾部空白。

## 完整相关回归

```text
.venv/Scripts/python.exe scripts/dev.py test packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_runtime/test_action_hook.py tests/test_runtime/test_g2_recovery_states.py tests/test_runtime/test_tool_receipts.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_runtime/test_kernel_purity.py tests/test_layering_domain.py tests/test_layering_entry_imports.py -q --tb=short
379 passed, 6 skipped in 134.03s
```

本轮移除进程内 DURABLE_TEST_POSTGRES_URL，每个旧 durable fixture 仅使用自身 SQLite 文件，避免其 drop_all 触及并行数据库。6 项跳过为 accepted_decision_pg 的 5 项 PG 专用测试，以及 worker_dispatch 中 1 项仅 PG 并发事务测试。前 5 项在下述 PG 矩阵实际通过；本轮没有把 worker_dispatch 的 PG 专用项计为通过。

修正后的 runtime 批处理新增文件共有 19 项；旧单动作测试 26 项保持零改动，合计 **45 passed**。这部分已包含在 379 中，不重复累计。

## SQLite / PostgreSQL 实测矩阵

使用本地 PostgreSQL 16 的专用临时数据库，经 DURABLE_TEST_POSTGRES_URL 注入；每个决策测试再使用独立 schema。未使用其他 worktree 的共享数据库，完成后删除专用临时数据库。

```text
.venv/Scripts/python.exe scripts/dev.py test packages/hecate-durable/tests/test_accepted_action_batch.py packages/hecate-durable/tests/test_accepted_action_bridge.py packages/hecate-durable/tests/test_accepted_decision_store.py packages/hecate-durable/tests/test_accepted_decision_pg.py -q --tb=short
116 passed in 100.03s
```

无跳过。批恢复文件为 8 个场景 × 双库，**16 passed**；同时回归既有单动作桥、决策存储及 PG 专用测试。此矩阵与上述回归存在交集，不相加为独立测试总数。

## 关键证据

- **固定原决策**：批初次读取的 content_digest 传到每个 dispatch；不同存储返回替换参数、同存储中途返回另一份有效但不同顺序的记录，均在业务调用前以 decision_conflict 停止，原前缀与未处理顺序保留。
- **错误语义**：中途不可用/损坏/记录消失统一为 decision_store_unavailable；ownership 失效为 stale_ownership；参数/装配校验为 invalid_action。单动作本身 needs_review / reconciliation_required 原值保留。取消和持久审批等待原样透传，后续零派发。
- **真实前缀恢复**：独立写进程在 A1 outcome 落盘后于下一 lookup 处 os._exit(9)，独立读进程回填 A1、继续 A2/A3；效果文件每动作一行，模型零调用。
- **真实重叠进程竞争**：race-first 在 A1 效果 fsync 后、回执落盘前等待屏障；race-second 独立解释器对同一数据库/owner 发起批处理，得到 A1 needs_review 且 A2/A3 未派发。释放首进程后其余动作完成；第三个 recover-all 进程禁止一切工具/模型调用，恢复全部结果、核对原 execution_id/tool_call_id 和业务载荷，每个动作效果仍恰一行。
- **真实换主继续**：A1 完成后原租约到期，新 holder/token 接管；旧 owner 派发 A2 失败，新 owner 回填 A1 后继续 A2/A3。
- **未知受保护写结果**：A2 实际产生副作用但 outcome 写入失败；返回 reconciliation_required。后续恢复仍 needs_review，A2 不重做、A3 不派发。
- **逐动作治理与三类终态**：failed/withheld/stopped 均停止；过期或接管发生在 A1 后，A2 被拒；初始非法 step 标识在存储零访问时拒绝。

首次写进程返回字典结果时，现有 executor 用 Python 字符串化，而 SQL 账本恢复用 JSON 序列化。新增精确文本比较曾据此失败；核实既有执行器和账本后，测试按真实业务载荷语义、稳定身份与状态比较，未改原序列化契约，未伪称首跑/恢复文本逐字节一致。

## 范围与限制

已 fetch origin/main，本 worktree 与主干基点均为 `9c95918a`。生产改动仅批处理新模块及单动作 helper/可选摘要绑定参数；没有改旧单动作测试、公共 conftest、平台 API/UI、Task/Run 控制、评测或 schema。

未提交、未推送、未归档。CI 全套和 PR review 需后续流程完成。本切片不提供多轮 Controller、Task 成功判定、主动续租或外部系统 exactly-once 保证。未知只读动作重试沿用既有共享执行契约，新增写动作故障证据不能泛化为所有执行方式的生产认证。
