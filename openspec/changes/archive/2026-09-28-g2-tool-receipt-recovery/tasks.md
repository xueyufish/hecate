# Tasks

## 1. 先建立失败复现(预期红)

- [x] 1.1 新增 G2 恢复缺口复现用例(`tests/test_runtime/test_g2_recovery_states.py`):①"写入成功→结果落盘前崩溃"窗口(手工构造含 `TOOL_CALL`、缺 `TOOL_RESULT` 的事件日志)后重新派发非幂等写,断言当前代码盲目重跑;②同 `execution_id` 不同参数重新派发,断言当前代码不拒绝;③模拟事件存储读取抛异常后重新派发 idempotent_write,断言当前代码 fail-open 重跑。运行确认 ①②③ 均按预期失败,并记录各自命中的缺口(claimed 混淆 / 无摘要 / store_unavailable 混淆)
- [x] 1.2 矩阵翻案用例:修改 `tests/test_runtime/test_tool_receipts.py::test_retry_decision_matrix` 中 `non_idempotent_write`/`external_side_effect`/`unknown` 类在 `failed` 回执下的期望为不自动重跑,运行确认红

## 2. 四态判定与恢复语义

- [x] 2.1 收紧 `tool_side_effects.should_auto_retry`:`failed` 回执仅 `readonly`/`idempotent_write` 返回 True;`non_idempotent_write`/`external_side_effect`/`unknown` 返回 False。验证:任务 1.2 转绿,`test_unregistered_tools_default_to_unknown` 等既有用例不回归
- [x] 2.2 新增恢复状态解析(如 `tool_worker.resolve_tool_execution_state`):按 `execution_id` 返回 `never_started`/`claimed`/`outcome_unknown`/`succeeded`/`failed`/`store_unavailable` 及关联 payload(`arguments_digest`、`result_digest`);事件存储读取异常显式映射 `store_unavailable`,历史事件缺 `arguments_digest` 字段按"摘要未知"兼容;`get_tool_receipt` 收敛为内部实现。验证:四态判定单测覆盖每种状态(含读库异常与缺字段历史)
- [x] 2.3 `TOOL_CALL`/`TOOL_RESULT` payload 记录 `arguments_digest`(canonical JSON:`json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` 的 SHA-256;arguments 解析失败按空参摘要)。验证:配对回执测试断言两事件摘要一致
- [x] 2.4 重写 ToolWorker 恢复分支:`claimed` 态写操作安全停止(人工核对)、`readonly`/`idempotent_write` 沿同一 `execution_id` 自动重跑;`store_unavailable` 仅放行 readonly;摘要不一致返回显式冲突错误不执行;`succeeded` 从通道历史(同 `tool_call_id` 的 role=tool 消息)回填真实结果,不可得时返回附 `result_digest` 的待对账标记,移除 `"[recovered]"`/`"[needs review]"` 占位文本中的伪造语义(保留明确的人工核对标记)。验证:任务 1.1 的 ①②③ 全部转绿,新增用例覆盖成功回填、待对账、冲突拒绝、claimed 两侧

## 3. 领取原子性

- [x] 3.1 `InMemoryEventStore` 覆写 `acquire_event_lock` 为真实 asyncio 锁(基类 no-op 只保证单次 append,不保证 check-then-act)。验证:并发 append/锁互斥单测
- [x] 3.2 ToolWorker 将"恢复状态查询 + `TOOL_CALL` 落盘"收进 `acquire_event_lock` 锁区,授权/审批/pre-hook 检查与执行留在锁外;未获首次领取权的执行者按观测状态处置,锁获取失败不自动重跑写操作。验证:`asyncio.gather` 双 worker 并发派发同一无回执动作键的测试——`port.calls` 恰一次、后到者停止;锁注入超时的测试——写操作停止

## 4. 契约与方案验收

- [x] 4.1 恢复状态查询与领取原子性语义进入 `tests/test_runtime/contracts/test_event_store_contract.py`,InMemory 与 Postgres 实现均通过。验证:`pytest tests/test_runtime/contracts -q` 全绿
- [x] 4.2 方案 §八验收矩阵前四行对应的四个故障注入场景作为最终验收用例落地并通过:"写入成功→结果落盘前崩溃"、读库失败、重复 worker、同键不同参数——证明无盲目重复写,恢复得到真实结果(通道历史可得的场景)。验证:用例全绿并在 PR 描述附方案 G2 关闭证据对照
- [x] 4.3 受影响面回归:`test_tool_receipts.py`、`test_tool_worker.py`、`test_tool_worker_wiring.py`、`test_chat_engine_convergence.py`、`tests/test_runtime/contracts/`。验证:scoped pytest 全绿,无因恢复语义变化产生的意外失败

## 5. 收尾

- [x] 5.1 仓库门禁四项:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、scoped pytest。验证:全部 0 错误
- [x] 5.2 更新 `docs/research/platform-evolution-baseline.md` G2 门槛记录:责任 change 指向本 change、补充关闭证据指针(用例位置与结果)。验证:文档与实际交付一致
- [x] 5.3 `openspec validate g2-tool-receipt-recovery` 通过,artifacts 与 tasks 状态一致。验证:命令输出无错误
