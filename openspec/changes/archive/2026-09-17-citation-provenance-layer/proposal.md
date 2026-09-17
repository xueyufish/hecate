# Citation Provenance Layer

## Why

1.3.5e（Hallucination Detection & Mitigation）的两个依赖已就绪：PostLLMHook ✅ + ContextEngine 4.13 ✅。业界调研（2026-09）表明：没有任何平台已将「claim 提取 → 证据检索 → 置信度打分 → block/warn/pass」作为运行时原语交付；Bedrock Guardrails 的 Contextual Grounding Check 依赖调用方显式传入 grounding source，而 Hecate 的投影是瞬态的（4.13 非破坏性）——"模型实际看到了什么"在调用结束后不可重建，这是运行时接地校验的结构性缺口。

分阶段结论（调研讨论已定）：溯源层先行。它把后续打分层最贵、最噪的一步（证据检索）降为按 ID 查表，同时其 session 级 chunk 注册表天然构成"模型实际看到了什么"的持久指纹——在不突破 4.13 非破坏承诺的前提下补上 grounding source 缺口。本 change 只做 Stage 1：溯源层 + 引用率风险信号；蕴含校验 judge、block/regenerate 处置、EnginePort.knowledge_query 兜底检索留待 Stage 2/3。

## What Changes

- **工具结果进入历史时做 chunk 切分与带内标记**：tool worker 写入工具结果消息时，按可配置粒度（默认 200–300 字符）切分，为每个 chunk 分配 `【N-M】` session 级递增编号并注入模型可见内容前缀；原始内容完整保留（程序化消费者不受影响）。
- **session 级 append-only chunk 注册表**：chunk ID 永不复用，跨轮可解析；投影中的丢弃/offload 不影响注册表解析——注册表即"进入过投影的内容"的持久带溯源指纹。
- **输出回映射**：对 LLM 响应做确定性引用提取（扫描 `【N-M】` 标记），产出 claim-to-chunk 引用映射，写入消息元数据并作为 EventStore 审计事件可查询。
- **引用率风险信号（warn 级）**：按响应统计事实性句段中无引用的占比，作为近零成本风险分写入审计事件（不拦截、不调用 judge 模型）。
- **per-node 策略配置**：对齐 `context_policy` 先例——启用开关、chunk 粒度、最小标记阈值可按节点配置，fail-fast 校验，canonical hash 入 agent 版本化。
- **与 4.13 的交互约束**：标记在工具结果创建时一次性写入（KV-cache 前缀安全）；`tool_result_truncation` 截断只砍尾部、标记在 chunk 头部存活，注册表记录部分存在；offload 后的 chunk 经 `recall` 恢复后注册表同步。
- **非目标（明确留待后续 change）**：蕴含校验 judge 后端、block/regenerate 处置动作、无引用 claim 的 knowledge_query 兜底检索、ADR-033 compaction 接地校验。

## Capabilities

### New Capabilities

- `citation-provenance`: 工具结果的带内引用溯源层——chunk 切分与 `【N-M】` 标记、session 级 append-only 注册表、输出回映射、引用率风险信号、审计事件，以及与投影生命周期（截断/offload/recall）的解析不变量。

### Modified Capabilities

- `agent-node-config`: 新增按节点配置 citation provenance（启用开关、chunk 粒度、最小标记阈值）的需求，策略解析与校验语义对齐既有 context processor chain 配置需求。

## Impact

- **代码**：`src/hecate/runtime/`（工具结果写入路径新增标记器；新的注册表与回映射模块；对齐 `context_policy.py` 的新策略解析）；`WorkerResult`/消息元数据携带引用映射。
- **数据形态**：工具结果消息的模型可见内容带 `【N-M】` 前缀（原始内容保留）——触碰 channel state 落盘形态，是本 change 相对"纯附加"最重的部分。
- **事件**：新增审计事件类型（chunk 注册、引用映射、引用率风险信号），EventStore 消费者需兼容（additive）。
- **性能**：标记开销约每截断结果 150–240 tokens（受最小标记阈值约束）；回映射为正则扫描，无模型调用。
- **API**：无 breaking；studio 侧引用徽标展示留待 Stage 2 一并交付。
