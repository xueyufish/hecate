## Why

当前上下文流水线以 `LLMWorker._apply_context_pipeline` 静态方法硬编码（截断 → 选择 → offload → 压缩 → 紧急截断五步冻结级联），上下文策略无法按节点/模型配置，重要性打分（4.12）的"中段丢弃"会破坏 prompt cache 前缀，规划中的日志级 LLM compaction（surface replacement）没有落点。

决策原则（用户确认）：**契约优先、以业界领先性为准、不受工作量约束**——凡活在契约层（接口、事件 schema、级别词汇表、策略解析规则）的能力全部进入本期设计；实现可分期，契约不分期。

## What Changes

- **新运行时扩展点**：`ContextProcessor` ABC + `ContextProcessorChain` 执行器（声明顺序 + satisfied 谓词提前停止 + 失败策略接缝：熔断/冷却/anti-thrash）。替代 `LLMWorker` 内硬编码五步级联；默认链与现有行为等价。
- **原子分组框架化**：链入口 `unitize()` 按 `tool_call_id` 关联消息为不可拆分组，配对不变式校验（对齐 Microsoft MessageGroup / dsh toolPairingBalanced）；处理器只操作组。
- **估算传感器抽象**：`TokenEstimator`（provider usage anchor 优先、chars/token 启发式兜底）——所有处理器的阈值正确性依赖它。
- **一方处理器**（T0 in-process，经 SPI 注册，符合 ADR-029）：
  - `BudgetWarnProcessor` — 预算告警以模型可见 HintBlock 持久注入（不动 system prompt，保 cache；即 4.17 Memory Pressure Alert 的落点）
  - `ToolResultTruncationProcessor` — 迁移现有 `_truncate_tool_results`
  - `RoundWindowProcessor` — cache 稳定区内的窗口选择；**importance 排序作为安全区内的丢弃优先级**（4.12 打分机制存续，角色从"选择策略"变为"排序信号"）
  - `OffloadProcessor` — 迁移现有 offload 保全逻辑为独立处理器
  - `ReloadProcessor` — 契约双向：显式 `recall` 工具按需重载被卸载块进 context
  - `CompressionProcessor` — 双后端接缝：投影式（现状语义）/ surface-replacement 式（日志级 compaction，本期只定 schema，实现为二期）
  - `KVCacheAwareProcessor` — 丢弃区域约束 + `cache_hint` 注解（4.11 shaping 层负责渲染为 provider 语法，如 Anthropic `cache_control`）+ cache 命中率指标发射
  - `HintProcessor` — 运行时状态注入（时间/任务/用量，AgentScope HintBlock 范式）
- **末级降级改为受控终止**：剥离 tool_calls 让本轮自然收尾，`stop_reason="token_capped"` 可观测（替代静默硬截断）。
- **策略解析层级**：node > agent > model-capability > global，含 per-model 阈值覆盖；配置 load-time fail-fast 校验；策略 `canonical_hash` 接入 agent 版本化。
- **BUDGET_SNAPSHOT 增量演进**：levels 词汇表增加 `warn`，新增 `stop_reason` 字段（对 8.20 回放等消费方向后兼容）。
- **compaction 事件 schema 本期定案（ADR）**：参照 dsh 四事件 bracket（`compaction/start` → `compaction/summary`（含 `shadowedSeqs`）→ surface replace → `compaction/end`），EventType 语义、fold 参与、checkpoint 源语义进入设计；实现随二期交付。
- 非破坏性不变量保留：链只产出投影，不改 channel snapshot 与事件日志原事件。

## Capabilities

### New Capabilities

- `context-processor-chain`：可插拔上下文处理器链契约——处理器 ABC 与结果契约、链执行器语义（顺序/提前停止/失败策略）、原子分组、TokenEstimator、策略解析层级、一方处理器集合及其行为、可观测性（BUDGET_SNAPSHOT 增量、stop_reason、cache 指标）、与 4.11 provider shaping 的注解/渲染分工。

### Modified Capabilities

- `context-engine`：`LLMWorker applies context pipeline before LLM invocation` 变更为 LLMWorker 委托 `ContextProcessorChain`（五步硬编码语义由默认链等价承载）；`Token budget resolution priority` 扩展 model-capability 维度。
- `context-offloading`：新增显式 `recall` 工具按需重载被卸载块的需求（在 read_file 之外的上下文重注路径）。
- `budget-governance`：`Three-level degradation strategy` 演进为 warn 告警 + 三级降级 + 受控终止终态；BUDGET_SNAPSHOT 词汇表扩展。
- `agent-node-config`：新增节点级 `context_processors` 配置（处理器选择、顺序、参数）与校验需求。

## Impact

- **代码**：`src/hecate/runtime/context.py`（保留 ContextEngine ABC，`estimate_tokens` 委托 TokenEstimator）、新增 `runtime/context_processors.py`（链契约与执行器）、`runtime/workers/llm_worker.py`（删除硬编码管线，改委托）、`runtime/offloader.py`（接入链）、`runtime/task_phase.py` 与 `runtime/evidence.py`（仅消费方不变）、`core/composition/`（链组装与策略解析）、`studio/workflows/execution_service.py`（默认链装配）、`runtime/pregel.py`（execution_context 透传扩展）。
- **事件契约**：BUDGET_SNAPSHOT 增量字段（向后兼容）；compaction EventType 为设计定案、二期实现，本期不改 EventStore schema。
- **跨特性依赖**：4.11（cache_hint 渲染）、4.10（BUDGET_SNAPSHOT 消费方兼容）、1.3.15b（offload 处理器化）、4.12（打分转排序信号）、4.17（HintProcessor 落点）、agent versioning（canonical_hash）。
- **文档**：`docs/design/engine-design.md`（扩展点清单）、`runtime/AGENTS.md`、`docs/features/feature-catalog.md`（4.13 交付状态；顺带勘误 OpenClaw "Context Engine" 过时归因）。
- **无数据库 schema 变更**、无删除性 API 变更；无 `context_engine` 时行为与现状完全一致（既有 spec 场景保留）。
