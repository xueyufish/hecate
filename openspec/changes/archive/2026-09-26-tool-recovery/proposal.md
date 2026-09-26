# Proposal: tool-recovery

## Why

外部安全评审 P1 #12：运行时对"状态可回放"与"外部动作可恢复"没有区分。三个具体缺口：

- **异常路径无回执**：`ToolWorker._execute_single_tool` 只在成功路径写 `TOOL_RESULT` 事件；工具抛异常时只返回错误消息，事件日志里留下一个**没有配对的 `TOOL_CALL`**——恢复时无法区分"执行失败"与"执行中/结果丢失"，重试语义无从谈起。
- **调用 ID 不可靠**：工具执行标识直接用 LLM 生成的 `tool_call_id`（`tool_call.get("id", "")`，可为空串），不是服务端分配的稳定执行 ID；同一逻辑调用的多次重试没有关联键。
- **Temporal 静默回退**：`TemporalWorkerPool.dispatch`（`runtime/temporal/worker_pool.py:81`）在占位状态下静默回退为本地直接执行——用户以为获得了分布式执行与崩溃恢复，实际什么都没发生；`run_worker.py` 注册的 Activities 为空列表，起一个空转 worker。

评审验收：外部操作成功但结果未持久化时杀进程，恢复后不盲目重发或重写；不可判定的结果进入人工核对状态。功能目录已注明 Temporal 限制——该注明应保留并升级为显式行为。

## What Changes

- **稳定调用 ID**：工具执行时由服务端生成 `execution_id`（UUID，独立于 LLM 的 `tool_call_id`），写入 `TOOL_CALL` / `TOOL_RESULT` 事件的 payload 与工具执行上下文；LLM `tool_call_id` 缺失时不再产生空串键。
- **副作用分类**：代码内置分类注册表（`readonly` / `idempotent_write` / `non_idempotent_write` / `external_side_effect` / `unknown`）——builtin 工具静态映射，自定义工具默认 `unknown`（按最保守的不可重试处理）。无数据库迁移（不改 ToolModel）。
- **回执语义**：`TOOL_RESULT` 事件补齐 `status`（`succeeded` / `failed` / `unknown`）与 `side_effect_class`；**异常路径补写 `TOOL_RESULT(status=failed)`**——消除"孤儿 TOOL_CALL"；`external_side_effect` 类工具执行前先写 `TOOL_CALL`（已有），结果不可判定（超时/连接中断）时写 `TOOL_RESULT(status=unknown)`，恢复逻辑据其进入人工核对而非盲目重试。
- **重试规则**：工具级重试查询回执——`readonly`/`idempotent_write` 可自动重试；`non_idempotent_write`/`external_side_effect` 仅在回执为 `failed`（确认未生效）时重试；`unknown` 一律不自动重试，标记人工核对。
- **Temporal fail-fast**：`TemporalWorkerPool.dispatch` 在占位状态下抛 `NotImplementedError`（含指引信息），不再静默回退本地执行；`run_worker.py` 在 Activities 为空时拒绝启动；功能目录的 Temporal 限制注明保留。

## Capabilities

### New Capabilities

- `tool-recovery`: 工具执行的回执语义（稳定调用 ID、副作用分类、结果状态）与未实现的 Temporal 分发选项的显式失败。

### Modified Capabilities

（无——`TOOL_CALL`/`TOOL_RESULT` 事件形状为 payload 内增强，事件类型契约不变，不构成既有 capability 的 requirement 变更。）

## Impact

- **代码**：`runtime/workers/tool_worker.py`（execution_id + 回执补全 + 分类查询）、新增 `runtime/tool_side_effects.py`（分类注册表）、`runtime/temporal/worker_pool.py` + `run_worker.py`（fail-fast）、`runtime/retry.py` 或 ToolWorker 内的重试判定（按回执）。
- **行为**：异常工具调用现在有配对的 `TOOL_RESULT(status=failed)` 事件（事件日志更完整，fold 不受影响——TOOL_* 本就是 fold-skipped 观测事件）；`TemporalWorkerPool` 从"假装工作"变为"明确报错"。
- **测试**：tool_worker 回执矩阵（成功/失败/unknown/分类）、Temporal fail-fast、重试判定矩阵。
- **无数据库迁移**；无 BREAKING API 变更（事件 payload 为增量字段，`log_schema_version` 语义按 ADR-030 增量事件处理）。
