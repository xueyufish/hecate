# Design — 4.13 Context Engine Processor Chain

## Context

现状与约束（动机见 proposal.md）：`LLMWorker._apply_context_pipeline` 是冻结的静态五步级联；`ContextEngine` 是已被 spec 钉死的三方法 ABC（`context-engine` capability），`PriorityContextEngine`（4.12）为 `WorkflowExecutionService` 生产默认；offload（1.3.15b）经 `execution_context["context_offloader"]` 注入并与 DROP 步骤耦合；BUDGET_SNAPSHOT（4.10）已被 8.20 回放与 budget-governance 消费。治理约束：ADR-029 将 `ContextEngine` 归为 K-C，T0 in-process 仅允许 engine 默认实现与 SPI 后的热路径处理器；waterfall 链（E3）语义为 kernel 安全不变量。决策原则：契约优先、业界领先、不受工作量约束（详见 proposal）。

## Goals / Non-Goals

**Goals**

- 把投影管线升级为一等扩展点：`ContextProcessor` 契约 + 链执行器，声明顺序 + satisfied 谓词 + 失败策略接缝。
- 契约层一次到位：原子分组、TokenEstimator、策略解析层级（node > agent > model-capability > global）、canonical_hash、BUDGET_SNAPSHOT 词汇表扩展、compaction 事件 schema（ADR 定案）。
- 默认链与现行为等价（warn 关闭时逐步等价），无 engine 时完全透传。
- KV-cache 一等公民：前缀稳定约束 + `cache_hint` 注解 + 命中率指标。

**Non-Goals**

- Studio 节点面板 UI（本 change 只到 API/schema 层；画布暴露归 Studio 路线图）。
- surface-replacement compaction 的**实现**（二期交付；本期产出 ADR + schema + 接缝登记）。
- 语义相似度/阶段启发的自动 reload（本期契约为显式 `recall` 工具；其余机制留作未来 ReloadProcessor 实现）。
- subagent/嵌套 workflow 预算聚合（deer-flow seen-message diff 模式，另立特性）。
- native server-side compaction 混合模式（进 compaction 二期 backlog）。
- EventStore schema 落地变更（二期才动；本期只写 ADR）。

## Decisions

### D1. 独立链，不挂入 waterfall（E3）

**选择**：`ContextProcessorChain` 是 runtime 内新的扩展点（裸名词 + `abc.ABC` 命名惯例），与 E3 waterfall 并列。

**否决**：复用 E3 `Chain`/`StageHandler`。理由：waterfall 语义是 ALLOW/BLOCK/SANITIZE + 单调拒绝 + 短路，是信任边界的**决策链**；处理器链是纯**投影链**——从不 block，自然控制流是"satisfied 即提前停"，SANITIZE 被滥用为 transform 会让审计语义失真。`engine-design.md` 已将两者定位为互补层次。ADR-029 同时禁止把链语义交给插件——独立链让 processor 只贡献策略、不动执行语义，天然合规。

### D2. ContextEngine 保留（混合形态），估算委托 TokenEstimator

**选择**：`ContextEngine` ABC 三方法契约不动（spec 已钉死、外部消费方在用）；`estimate_tokens` 内部委托 `TokenEstimator`。选择/压缩逻辑作为可参数化能力被 `RoundWindowProcessor` / `CompressionProcessor` 消费（4.12 打分成为排序信号，见 D4）。

**否决**：完全溶解（引擎只剩 estimator）——破坏既有 spec 场景与消费方，收益仅是抽象洁癖；链在引擎旁复制一份估算——双传感器漂移。`engine` 与 `chain` 的分工固化为：**引擎 = 基元（估/选/压），链 = 编排与策略**。

### D3. 原子分组是框架职责（unitize 前置）

**选择**：链入口一次 `unitize()`：按 `tool_call_id` 把 assistant(tool_calls) + tool results 折叠为不可拆 `ContextUnit`；同时做配对不变式校验（悬挂 tool result → 附加合成输出或整组丢弃；multiset 校验对齐 CubeLoop `ToolCycleViolation`）。处理器只操作 unit 序列，输出再展平为消息。

**否决**：每个处理器自行处理关联——N 份重复实现，`RoundWindowProcessor` 切散 tool pair 后由 4.11 静默丢孤儿的 bug 链正是调研中 Microsoft（MessageGroup）/ dsh（toolPairingBalanced）/ CubeLoop 三家独立收敛防御的同一坑。

### D4. importance-within-safe-region：打分与缓存正交合成

**选择**：`RoundWindowProcessor` 默认最旧先丢（滑窗语义，system 与最新 user unit 钉住）；importance 排序模式开启时，打分只决定**可丢区内**的丢弃优先级，绝不破坏受保护前缀。`PriorityContextEngine` 打分函数原样复用（recency + role bonus + 失败/超长工具结果惩罚）。

**理由**：importance 与 recency 是正交维度——前者是优先级信号，后者是放置约束，合成为"放置约束 × 优先级排序"；4.12 机制完整存续，仅角色变化。

### D5. KVCacheAware 只决策，渲染归 4.11

**选择**：处理器输出 `cache_hint` 注解（切点位置），`context_shaping`（4.11）在 provider 边界渲染为具体语法（Anthropic `cache_control` 等）；无 cache 能力的 provider 丢弃注解。命中率指标从 provider usage 的 cached/total 字段计算，随调用遥测发射（kernel 只 emit，OTel 导出）。

**否决**：处理器直接写 provider 语法——破坏 provider 无关边界，且与 4.11 职责重叠。此分工让 breakpoint 管理名正言顺落在本 capability。

### D6. 末级降级 = 受控终止（用户已确认）

`stop_reason="token_capped"` + 剥离末尾 assistant 的 pending tool_calls 让循环自然收尾；替换 `_emergency_truncate`。`_hard_truncate_message` 保留为单条超预算消息的预处理特例（与"最终级"语义不同）。BUDGET_SNAPSHOT `levels` 词汇表扩为 `warn|drop|compress|terminate`，新增 `stop_reason` 字段——additive，8.20 回放旧事件照常读。

### D7. 失败策略内聚于执行器

熔断（连续 N 败 → open，M 次成功 fallback → half-open）、冷却阶梯（默认 60s→300s→900s，对齐 Hermes）、anti-thrash（最小节省比 + 最少新消息数，对齐 CubeLoop）作为 `FailurePolicy` 对象注入执行器——**接缝是契约**，默认参数可调。摘要失败冷却只约束 CompressionProcessor 的 LLM 调用，不阻塞非 LLM 处理器。

### D8. 策略解析与 canonical_hash

解析顺序 **node > agent > model-capability > global**；model-capability 维度从 model metadata（复用既有 model-metadata-schema）读 prompt-caching 支持与窗口大小：cache-capable → KV-aware-first 默认策略，否则滑窗默认。load-time fail-fast 校验（未知类型/字段、互斥参数——dsh 惯例）。`canonical_hash` 对 resolved policy 计算（规范化 JSON + sha256），挂到 agent version 元数据——衔接既有 agent-versioning；对齐 deer-flow `release_policy_parameters()` 思路但落到版本化体系。

### D9. TokenEstimator 多档传感器

接口：`estimate(messages) -> int`。优先级：**provider usage anchor**（`AnchorTokenEstimator`——以最后一次 LLM 响应的 provider usage 为锚，内容字符覆盖长度标定锚点范围，新消息按启发式增量）→ **legacy engine 估算**（engine-only 兼容路径用 `ContextEngine.estimate_tokens` 自身作传感器，保证旧引擎行为完全平移）→ **启发式兜底**（4 chars/token）。**hint 注入判定使用注入前基线** + once-per-crossing 去重，避免"hint 自身推高 usage 再触发 warn"的自引用循环。

### D10. compaction 事件 schema：本期 ADR 定案，二期实现

以 dsh 四事件 bracket 为模板映射到 Hecate EventStore（1.3.19）：

| dsh | Hecate 拟定（终名在 ADR 定） |
|---|---|
| `compaction/start`（log-only 锁） | `COMPACTION_STARTED`（payload: trigger, threshold 快照） |
| `compaction/summary`（摘要 + shadowedSeqs） | `COMPACTION_SUMMARY`（summary node 内容 + `shadowed_seqs` 引用 + usage） |
| surface replace（唯一 surface mutation） | `CONTEXT_SURFACE_REPLACED`（`{start_seq, end_seq}`，fold 参与 derive-messages 路径，对齐 1.3.19 B2 日志推导恢复） |
| `compaction/end` | `COMPACTION_COMPLETED` |

要点：bracket 成对构成日志级锁，orphan start = in-progress；`shadowed_seqs` 保证 replay 可还原原事件；compaction 事件为 checkpoint 源（复用 dsh `compactCheckpointSource` 身份思路）；未知 EventType 回读回落 `CUSTOM` 的既有兼容机制沿用（1.3.4 先例）。**本期动作**：ADR 文档 + 消费方接缝登记（8.20 回放、time-travel-resume、ADR-030 下游消费者接缝登记表补行）+ `CompressionProcessor` 预留 backend 接口。**不改 EventStore 代码**。

### D11. LLMWorker 迁移与装配

`_apply_context_pipeline`/`_emergency_truncate`/`_truncate_tool_results` 的逻辑迁入处理器；LLMWorker 只保留"从 execution_context 取链 → 调用 → 接收 (messages, chain_report)"。装配在 `core/composition`：默认链（warn 关闭时与旧五步逐步等价）注入 `PregelRuntime`，经 `execution_context["context_chain"]` 透传；`context_engine` 键保留向后兼容（无链时旧路径）。`WorkflowExecutionService` 两处装配点改传默认链。

### D12. recall 工具

注册为 builtin tool（走既有 tool registry），始终注册、路径不存在返回错误结果（spec 已定）；读环境文件后**原样返回 JSON 消息块**作为 tool result——不写 channel、不重排历史，与 1.3.15b 的 read_file 路径互补（read_file 给原文文件，recall 给结构化消息块）。

### D13. hint 与投影层的持久性

HintBlock/warn hint 注入**投影层**（每次调用由链从会话级元数据重放注入），不写 channel——满足"后续轮可见"语义，又不污染 fold/回放/事件日志。时间戳等易变值只存在于 hint 生成时刻（对齐 Manus "prefix 内避免秒级时间戳"的 cache 纪律：hint 追加在上下文尾部，不动前缀）。

## Risks / Trade-offs

- [末级行为从静默截断变为受控终止 → 依赖旧行为的隐式预期破裂] → 等价性测试只钉 warn 以下路径；terminate 路径新增显式行为测试；feature-catalog 变更说明明示语义变化。
- [锚点缺失时启发式对 CJK 偏差大 → 阈值失真] → estimator 已接口化，可在不换契约下替换实现；首调用（无锚）窗口短，风险可接受，文档标注。
- [hint 累积挤占预算] → once-per-crossing 去重 + hint 计入估算 + hint 上限条数。
- [compaction schema 与 1.3.19 fold/8.20 回放冲突] → 本期只定 ADR + 接缝登记，实现前强制复核消费方清单（复用 1.3.4/9.4 的接缝登记先例）。
- [默认链等价性回归] → 黄金等价测试：同一输入下旧管线输出 == 默认链输出（逐消息断言）；旧管线函数删除前先并行存在一轮。
- [处理器数量增长导致顺序错误] → load-time 校验 + 有损次序告警（compress 先于 window 时告警不拒绝）；canonical_hash 让策略变化可审计。
- [T0 in-process 处理器的信任边界] → ADR-029 T0 明确允许 "engine ABC defaults & hot-path processors behind SPIs"；第三方处理器仍必须走注册表白名单（配置校验只接受注册类型），与"安装产物不给 T0"一致。

## Migration Plan

1. **落地链契约与处理器**（新旧并行）：新模块 + 默认链装配；`LLMWorker` 切换为委托；等价性测试通过后删除旧静态管线函数。无 flag——装配即切换，回滚 = revert（无 schema/DB 变更）。
2. **BUDGET_SNAPSHOT 字段扩展**随步骤 1 上线（additive，消费方无须改动）。
3. **二期（独立 change）**：surface-replacement CompressionProcessor 后端 + EventStore EventType 落地 + checkpoint 源接入 + Studio 面板（如立项）；前置条件 = 本期 ADR + 消费方接缝登记完成。

## Open Questions

- compaction 四事件的最终枚举命名、fold 可见性细节（summary 事件是否参与 channel fold 还是仅 derive-messages 路径）——ADR 内解决，不阻塞本期实现。
- model-capability 元数据中 prompt-caching 标记的字段来源细节（model-metadata-schema 既有字段 vs 新增）——实现时对齐，不影响契约。
