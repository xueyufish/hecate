# Design: tool-recovery

## Context

- **事件底座现成**：`EventType.TOOL_CALL` / `TOOL_RESULT` 已存在（ADR-030 增量契约，fold-skipped 观测事件），payload 为自由 dict。当前 TOOL_CALL payload：`{tool_name, arguments, tool_call_id, log_schema_version}`；TOOL_RESULT payload：`{tool_name, result_length, tool_call_id, log_schema_version}`——无状态、无分类。
- **发出点单一**：`runtime/workers/tool_worker.py` 的 `_execute_single_tool` 是工具执行的唯一 choke point（1.3.5e 引用溯源已在此 choke point 落点，先例成立）。成功路径写 TOOL_RESULT；**异常路径（511-528 行）只返回错误 result，不写 TOOL_RESULT**——孤儿 TOOL_CALL 的来源。
- **调用 ID**：`tc_id = tool_call.get("id", "")`——LLM 临时 id，可空。会话内 `denial_tracker` 已按 tc_id 去重，说明会话级标识有先例。
- **Temporal**：`TemporalWorkerPool.dispatch` 尾行 `return await worker.execute(...)` 即静默回退；`run_worker.py` 的 `Worker(activities=[])` 空注册可启动；两者均无生产调用方（`TemporalWorkerPool` 全仓库零消费）——fail-fast 的风险窗口为零。
- **重试**：`RetryExecutor` 是 node 级（WorkerResult 粒度）；工具级重试语义目前不存在，本 change 新建而非改造。

## Goals / Non-Goals

**Goals:**

- 每次工具执行有服务端稳定 `execution_id` 与完整回执（配对的 TOOL_CALL/TOOL_RESULT，含状态与副作用分类）。
- 恢复判定有据可依：readonly/idempotent 可重试，非幂等仅确认未生效时重试，unknown 进人工核对。
- Temporal 占位显式失败。

**Non-Goals:**

- 不实现真正的 Temporal 分发（Activities 注册、Workflow 编排）——那是独立的大工程，本 change 只让它不再撒谎。
- 不做跨进程恢复的执行器（"杀进程后恢复"的调度主体——选择 Temporal 还是持久化作业体系——是评审明说的二选一决策，后续 change 落地；本 change 交付其依赖的回执语义）。
- 不改 ToolModel（副作用分类不进数据库，代码内注册表；分类进库留待工具平台后续迭代）。
- 不改 fold 语义——TOOL_* 事件本就 fold-skipped，payload 增量不影响 channel 状态。
- 不动 B1b 的聊天循环迁移（那是下一个 change）。

## Decisions

### D1: `execution_id` 由服务端生成，`tool_call_id` 保留为关联字段

`execution_id = uuid4()` 在 `_execute_single_tool` 入口生成，贯穿 TOOL_CALL/TOOL_RESULT payload 与 tool_context。理由：LLM 的 tool_call_id 可能缺失、可能跨会话重复，不能做恢复键；但保留它以维持与消息历史、denial_tracker 的既有关联。备选（否决）：用 `tc_id` + superstep 复合键——tc_id 为空时退化，且复合键查询复杂。

### D2: 副作用分类 = 代码内注册表，内置静态映射 + 默认 unknown

新增 `runtime/tool_side_effects.py`：`SideEffectClass` StrEnum + `classify(tool_name) -> SideEffectClass`。内置映射示例：web_search/memory 检索类 → `readonly`；load_skill/skill 安装类 → `idempotent_write`；send-message/写文件/shell 执行类 → `external_side_effect` 或 `non_idempotent_write`（按 builtin 清单逐个标注）；其余 → `unknown`。分类进事件 payload，恢复逻辑据此判定。备选（否决）：ToolModel 加列——需要迁移 + 管理界面，内置清单已覆盖绝大多数执行面；自定义工具默认 unknown 最保守，安全。

### D3: 回执 = 增强 TOOL_RESULT payload，异常路径补写

TOOL_RESULT payload 增加：`execution_id`、`status`（succeeded/failed/unknown）、`side_effect_class`、失败时 `error`（脱敏摘要）。异常路径补写 `TOOL_RESULT(status=failed)`；`external_side_effect`/`non_idempotent_write` 类工具在执行阶段抛出**不可判定异常**（超时、连接中断——按异常类型判定，新定义 `ToolOutcomeIndeterminateError` 或按异常标签）时写 `TOOL_RESULT(status=unknown)`。TOOL_CALL payload 增加 `execution_id` 与 `side_effect_class`。`log_schema_version` 语义不变（payload 增量字段，fold 跳过）。

### D4: 重试判定进 ToolWorker 的恢复查询函数，而非改造 RetryExecutor

新增 `tool_receipt_query(session_id, execution_id) -> ReceiptState`（读事件日志配对状态）与 `should_auto_retry(classification, receipt) -> bool` 纯函数。本 change 交付判定函数与单测；**消费方**（恢复流程调用它）属 B1b 的断线重连语义——本 change 不接线自动重试，只把"能安全判定"做好。备选（否决）：改 RetryExecutor——它是 node 粒度，工具粒度塞进去会混淆层次。

### D5: Temporal fail-fast 的两个落点

`TemporalWorkerPool.dispatch` 首行抛 `NotImplementedError("Distributed dispatch via Temporal is not implemented; ...")`——保留类与构造函数（类型层兼容），执行必失败。`run_worker.py` 在 `activities` 为空时 `SystemExit` 并输出注册指引。功能目录 Temporal 行保留原有注明（本 change 使"注明"与行为一致）。零消费方意味着无迁移成本。

## Risks / Trade-offs

- [事件量增加] 异常路径补写 TOOL_RESULT 使事件数略增 → 观测事件本就逐条记录，增量仅限失败路径。
- [不可判定异常的判定面] 哪些异常算"结果不可判定"（超时/连接类）vs 明确失败（参数错/权限拒）→ 按 builtin 工具的异常形态枚举初始清单，`unknown` 判定从紧（宁可人工核对不可盲目重试）。
- [分类清单维护] 内置工具新增时可能漏登记 → 默认 unknown 兜底 + PR 模板提醒；后续可加 CI 检查（builtin 清单与注册表 diff）。
- [Temporal 使用者断裂] 显式构造 TemporalWorkerPool 的代码开始抛异常 → 全仓库零消费方，风险为零；报错信息含迁移指引。

## Migration Plan

1. 合入即生效：事件日志开始携带 execution_id/status/side_effect_class。
2. 存量日志中的孤儿 TOOL_CALL（历史数据）不做回填——恢复逻辑按"无回执 = unknown"处理，天然安全。
3. Temporal：无消费者，无需迁移。
4. 回滚 = revert（无迁移）。

## Open Questions

无——不可判定异常清单在实现时按异常类型枚举，判定从紧的原则已定。
