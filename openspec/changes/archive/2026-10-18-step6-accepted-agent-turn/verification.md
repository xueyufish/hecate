# 验证记录 — step6-accepted-agent-turn

以下为复核修正后的实际结果。GLM 原新增 SQLite 套件 **19 passed**，原共享检查也通过。新增入口回归在原实现上为 **5 failed、17 passed**，复现非法上下文/ownership 仍进入决策阶段或被隐式作为空上下文的问题；修正后全部通过。首次补测中两项因测试误用 executor 的 policy 参数名失败，已改成真实 access_policy 后重跑，不把测试装配错误计作产品缺陷。

## 共享检查

```text
.venv/Scripts/python.exe scripts/dev.py check all
ruff check: All checks passed!
ruff format --check: 1609 files already formatted
mypy: Success: no issues found in 958 source files
```

`openspec validate step6-accepted-agent-turn --strict`、`git diff --check` 通过；另逐行检查所有未跟踪源码/artifacts 的尾部空白。

## 完整相关回归

```text
.venv/Scripts/python.exe scripts/dev.py test packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_runtime/test_action_hook.py tests/test_runtime/test_g2_recovery_states.py tests/test_runtime/test_tool_receipts.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_runtime/test_kernel_purity.py tests/test_layering_domain.py tests/test_layering_entry_imports.py -q --tb=short
410 passed, 6 skipped
```

显式收集两个 package 的全部测试，覆盖 DecisionStep、单动作、批处理、PortDecisionResponder、自足与分层边界。该轮清除进程内 DURABLE_TEST_POSTGRES_URL，旧 fixture 仅使用每例独有 SQLite 文件。6 个跳过分别为 accepted_decision_pg 的 5 项 PG 专用测试，以及 worker_dispatch 中 1 项需要 PG 并发 writer 事务的测试；前 5 项在下述矩阵实际通过，worker_dispatch 的 PG 专用项本轮不计为通过。

新 turn runtime 文件最终为 23 项，durable 文件为 8 项，均已包含在本轮回归；原单动作和批处理测试文件未修改。

## SQLite / PostgreSQL 矩阵

本地 hecate-step6-pg 映射宿主端口 55432。原设计的“本地无法运行 PG”说明不符当前环境；本 worktree 缺已声明驱动，使用 `.venv/Scripts/python.exe scripts/dev.py sync --profile postgres` 经 uv --locked 安装 psycopg 相关包，未修改 pyproject/lock。使用独立临时数据库，每例再隔离 schema；完成后清理该临时库，不占用其他 worktree 的库。

```text
.venv/Scripts/python.exe scripts/dev.py test packages/hecate-durable/tests/test_accepted_agent_turn.py packages/hecate-durable/tests/test_accepted_action_batch.py packages/hecate-durable/tests/test_accepted_action_bridge.py packages/hecate-durable/tests/test_accepted_decision_store.py packages/hecate-durable/tests/test_accepted_decision_pg.py -q --tb=short
132 passed
```

无跳过；新 turn 为 8 场景 × SQLite/PostgreSQL，16 项，另回归既有批恢复、单动作桥与决策存储。此轮与完整相关回归有交集，不相加为独立测试总数。

## 关键断言

- malformed context/execution_context/ownership 在 lookup、模型、commit 与业务调用前拒绝；合法 None 的既有约束保持，租约仍由真实存储/执行链核验。
- 在实际业务调用内部确认决策已提交，观察 respond → commit-start → commit-finished → business 的顺序；不是仅事后检查 accepted_at。
- fresh commit 与恢复命中两种 T1/T2 窗口替换记录均显式冲突，业务调用和认领为零；可选 batch 摘要缺省路径保持兼容。
- 真独立解释器在 commit 后或 A1 outcome 落盘后 os._exit(9)；恢复响应方零调用，完整原决策不变，A1 原结果、稳定 execution_id 以及后续每个业务载荷核验，效果文件每动作一行。
- A2 效果落地但回执写入失败，首进程返回 reconciliation_required；recover-unknown 独立进程禁止全部工具/模型调用，回填 A1、A2 needs_review、A3 未派发，两个已发生效果不重做，Task 状态保持不变。
- 决策 commit 后首动作前租约到期：首动作 stale_ownership，所有业务调用和账本认领为零；缺 owner 在真实 SQL store 无法接受决策。
- 取消与 ApprovalWaitSignal 从真实审批回调抛出并原样传播；A1 后的动作不派发。没有自动下一回合、Task 成功或完成性判断。

业务 fixture 字典首跑由既有 executor 字符串化，账本恢复 JSON 化；按真实业务载荷语义与稳定身份核对，不伪称文本字节一致，也未修改既有序列化/只读重试契约。

## 工作区与边界

已 fetch origin/main，基点及已检查主干均为 f6a696b4。生产改动仅 AgentTurn 新模块及 batch 可选期望摘要扩展；不改旧批/单动作测试、公共 conftest、平台 API/UI、Task/Run 控制、schema、功能清单或其他 worktree。

未提交、未推送、未归档。CI 全套和 PR review 留待后续流程。技术组合与脚本化故障证据不等于多回合 Controller、真实模型质量或生产场景认证。
