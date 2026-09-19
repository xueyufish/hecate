# Proposal

## Why

4.13 的 processor chain 只完成了瞬态投影半边：每轮调用对完整历史重做 O(N) 投影，fold 输入永不收缩，长会话的 resume/重放成本随历史单调增长。ADR-033 已把 durable compaction（surface replacement）的事件 schema 定为 normative，实现被顺序化为本变更；当前 `CompressionProcessor(backend="surface_replacement")` 选择即 fail-fast。

## What Changes

- **事件分类扩展（additive）**：新增四个 `EventType`——`COMPACTION_STARTED`、`COMPACTION_SUMMARY`、`CONTEXT_SURFACE_REPLACED`、`COMPACTION_COMPLETED`；全部为 bookkeeping 事件，fold 语义零改动，不删除任何既有事件。
- **surface_replacement backend 落地**：`CompressionProcessor` 的 durable 分支——容量轴触发（PRE_STEP 测有效 surface，不受预算轴 `when_over_budget` 短路）、bracket 事件写入、摘要生成、投影替换；替换默认 0.8×context window，尾部保留默认 0.16。
- **影子化账本视图**：chain 入口公共路径构建「有效 surface」（channel 应用 `CONTEXT_SURFACE_REPLACED` 账本后的派生视图），对所有链无差别生效；账本条目携带区间端点内容哈希（锚点），应用前校验，失配 fail-open 放行原文并靠触发线自愈。
- **CompactionSummarizer extension point**：runtime 内 ABC（plain noun 规范），生产实现在 composition 层走 `RuntimePort.llm_invoke`；路由 pin 与 cache namespace 隔离不进 runtime。
- **摘要 QA 门槛**：拒绝「不缩小」的摘要；校验失败 = 压缩未发生（surface 原样，流程落到既有 TerminationProcessor 兜底）。
- **互斥 fail-fast**：任一节点链含 surface_replacement 且 messages 通道 eviction policy ≠ `NoEviction` 时拒绝启动。
- **window 注入管道**：`ModelProvider.max_context`（字段已存在）→ `execution_context["context_window"]` → 模型窗口提示表兜底 → 均无法解析且启用该 backend 时装配期 fail-fast；compression 配置面新增 `trigger_ratio` / `retain_ratio`。
- **rolling 再压缩**：第二次压缩吸收上次摘要节点（`prior_compaction_id` 链），新区间按 channel 坐标覆盖旧区间；checkpoint metadata 记录 `last_compaction_id`。

**非目标**：conversation store（`conversation_load/save` 面）不动；8.20 replay UI 不做（仅保数据契约）；in-loop recall 不做（design 记 extension point）；overflow bypass（provider `CONTEXT_WINDOW_EXCEEDED` 甄别）裁剪为 follow-up——现状无任何甄别逻辑；生产 `context_budget` 注入缺失为独立 follow-up。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `context-processor-chain`：细化「Compression backend selection」需求——触发测量点（有效 surface、PRE_STEP、容量轴独立于预算轴）、QA 门槛、失败语义、rolling 再压缩；新增「影子化账本视图」（锚点校验、fail-open 自愈、对所有链无差别生效）与「压缩排他性及窗口解析」（互斥 fail-fast、window 来源优先级、新参数）两条需求。
- `execution-state-log`：新增压缩 bracket 事件分类需求——四事件的载荷契约（含 `start_anchor`/`end_anchor`、`prior_compaction_id`）、bookkeeping/fold-skip 语义、原文不可删除约束。

## Impact

- **runtime**：`context_processors.py`（backend 分支、账本视图、触发判定、QA）、`context_policy.py`（参数校验、排他校验、窗口兜底）、`eventstore.py`（EventType +4）、`workers/llm_worker.py`（chain 入口接线，若有）。
- **composition**：`studio/workflows/execution_service.py` 与 `core/composition/runtime_port_adapter.py`（summarizer adapter、`context_window` 注入、排他校验挂载）。
- **数据模型**：无迁移——`ModelProvider.max_context` 字段已存在。
- **测试**：`tests/test_runtime/test_context_processors.py`（backend/触发/账本/锚点）、`tests/test_runtime/test_logpolicy_fold.py`（fold 对等性）、新增 bracket 锁与 resume 场景。
- **文档**：ADR-033 落地状态注释更新；两条 ADR 澄清记入 design（`CONTEXT_SURFACE_REPLACED` 的「surface mutation」措辞、payload 扩展字段）。
