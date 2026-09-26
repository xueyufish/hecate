# Tasks: tool-recovery

## 1. 副作用分类注册表

- [x] 1.1 新增 `runtime/tool_side_effects.py`：`SideEffectClass` StrEnum（readonly/idempotent_write/non_idempotent_write/external_side_effect/unknown）+ `classify(tool_name) -> SideEffectClass`（builtin 静态映射，默认 unknown）+ `should_auto_retry(classification, receipt_status) -> bool` 纯函数。验证：单测覆盖每类判定分支。
- [x] 1.2 内置工具清单标注：逐个登记 `tools/tool/builtin` 注册的工具到映射（检索/记忆读取 → readonly；文件/shell/发送类 → 写或外部；清单外的留 unknown）。验证：与 builtin 工具名集合 diff 无遗漏（测试断言）。

## 2. 回执语义接入 ToolWorker

- [x] 2.1 `runtime/workers/tool_worker.py`：`_execute_single_tool` 生成服务端 `execution_id`；TOOL_CALL payload 增加 `execution_id` + `side_effect_class`；成功路径 TOOL_RESULT 增加 `execution_id/status=succeeded/side_effect_class`；**异常路径补写 `TOOL_RESULT(status=failed)`**（含 error 摘要）。验证：`mypy` 通过。
- [x] 2.2 不可判定异常识别：超时/连接类异常 → `TOOL_RESULT(status=unknown)`（判定清单从紧，仅明确枚举的异常类型）。验证：单测。
- [x] 2.3 回执查询：新增按 `execution_id` 读取配对状态的查询函数（供恢复流程与测试使用）。验证：单测（InMemoryEventStore 上查成功/失败/无回执三种）。

## 3. Temporal fail-fast

- [x] 3.1 `runtime/temporal/worker_pool.py`：`dispatch` 首行抛 `NotImplementedError`（含"分布式分发未实现"指引），删除静默回退；类与构造保留。`run_worker.py`：`activities` 为空时拒绝启动（SystemExit + 注册指引）。验证：单测（dispatch 抛错、run_worker 空注册拒绝）。
- [x] 3.2 功能目录 Temporal 限制注明保留并核对措辞与行为一致。验证：文档自查。

## 4. 测试与门禁

- [x] 4.1 回执矩阵测试（`tests/test_runtime/`）：成功（配对 + succeeded）/ 异常（failed 补写）/ 不可判定（unknown）/ tc_id 为空（execution_id 仍在）/ 分类进 payload。验证：全绿。
- [x] 4.2 重试判定矩阵测试：readonly 中断可重试；non_idempotent failed 可重试；unknown 状态不重试；未登记工具不重试。验证：全绿。
- [x] 4.3 门禁：`ruff check src/hecate/ tests/`、`ruff format --check`、`mypy src/`、`python -m pytest tests/test_runtime tests/test_api tests/test_services -q`。验证：全部 0 错误。
- [x] 4.4 `openspec validate tool-recovery` 通过。
