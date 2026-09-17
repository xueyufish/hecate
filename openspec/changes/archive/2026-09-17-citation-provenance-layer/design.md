# Design — Citation Provenance Layer

## Context

4.13 已交付可插拔上下文处理器链：投影瞬态、非破坏、每次降级发 `BUDGET_SNAPSHOT`。这带来运行时接地的结构性缺口——"模型实际看到了什么"在调用结束后不可重建，后续打分层（1.3.5e Stage 2）将没有 grounding source 可用（Bedrock 模式中该输入由调用方显式提供）。业界调研结论（见 proposal Why）：溯源层先行，把打分层的证据检索降为查表。本 change 的行为契约见 specs（citation-provenance + agent-node-config delta）。

## Goals / Non-Goals

Goals:
- 工具结果进入历史时一次性完成 chunk 切分与 `【N-M】` 带内标记；session 级 append-only 注册表跨轮可解析。
- 响应侧确定性回映射 + 引用率风险信号（warn 级、纯观察、无模型调用），全部走 EventStore 审计事件。
- per-node 策略配置，默认关闭，语义对齐 `context_policy` 先例。

Non-Goals:
- 蕴含校验 judge、block/regenerate 处置、knowledge_query 兜底检索（Stage 2）。
- compaction 接地校验（ADR-033 后端落地时）。
- 引用准确性的验证——Stage 1 只回答"引用了什么"，不回答"引用得对不对"。
- studio UI 徽标展示。

## Decisions

### D1: 带内标记为主（而非带外索引）
模型自报引用（复制可见的 chunk 编号），后续打分层只做蕴含校验。Alternatives：Bedrock 式带外（scorer 独立承担 claim-chunk 模糊匹配，噪声大、成本高）；纯带外索引（无需模型配合，但检索匹配承担全部误差）。选带内的理由：(a) 匹配问题在写入时一次性解决，回映射是正则；(b) 引用约定本身改善模型的接地行为（RAG 文献中 citation instruction 一致降低幻觉率）；(c) 引用的*存在*是弱证据，*准确性*留给 Stage 2 judge——两层互补而非冗余。

### D2: 标记写入 channel state（工具结果创建时），而非 chain 投影注入
标记在 tool worker 写入路径执行一次，之后内容字节稳定：跨轮 ID 永不复用、KV-cache 前缀安全（对比 Hermes micro-compaction 每轮破坏缓存的反例）、注册表与历史天然一致。代价是触碰落盘形态（本 change 最重的部分）。Alternative：chain 内瞬态注入——非破坏性更"纯"，但标记每轮重算、ID 不稳定、缓存前缀被破坏，注册表退化为投影元数据，线索 A 的"持久指纹"红利消失。原始内容保留在消息记录中，程序化消费者不受影响。

### D3: 注册表 = 内存对象 + 审计事件可重建
`CitationRegistry` 为 session 级内存对象（生命周期对齐 `ContextChainFactory` 的 chain 缓存模式），chunk 注册/引用映射/风险信号以 additive CUSTOM 事件（`CITATION_REGISTERED` / `CITATION_MAP` / `CITATION_RISK`，payload 模式对齐 `BUDGET_SNAPSHOT`）入 EventStore；会话重启后按事件重放重建，重建前的引用记为 unresolved 而非报错。不新增数据库表。

### D4: 回映射与风险信号在 worker 响应路径实现，接口按 stage 形状切
Stage 1 在 LLMWorker 响应路径上以确定性步骤实现（与 `chain_report` 同样的传递模式，结果写入消息元数据）；模块边界按未来的 middleware `LLM_RESPONSE` stage 形状设计，Stage 2 引入 judge stage group 时迁移是局部的。Alternative：现在就挂 PostLLMHook——但 hook 的 `GuardrailResult` 通道不携带元数据旁路，为纯观察行为引入 stage 编排属于过度设计。

### D5: 引用指令以一次性 hint 消息注入，不改 system prompt
模型需要知道引用约定才有配合率。对齐 4.13 原则（hints never modify the system prompt）：启用节点的会话在首条用户消息后注入一次 user-role 指令消息（引用格式与何时引用），持久于历史。提供配置开关；不自动改写 agent 的 system_prompt。

### D6: 只标记纯文本 str content；截断/压缩依赖既有尾部语义
非字符串 content（结构化块、None）Stage 1 原样通过——标记器不做内容类型适配。`tool_result_truncation` 只在尾部追加截断指示（既有实现），chunk 头部标记天然存活；窗口选择/offload/compress 整单元操作，不动单元内文本，注册表解析不受投影降级影响——两条交互路径都不需要修改 4.13 代码，只需要注册表记录 partial/truncated 状态。

### D7: 策略解析独立成 `provenance_policy.py`，复用 `context_policy` 的校验风格
fail-fast 校验、canonical hash 贡献（并入 agent 版本化）、默认关闭。不并入 `context_policy.py`——单一职责，且 citation 策略与 chain 策略的解析时机不同（chain 在每次 apply 前解析，citation 在节点装配时解析一次）。

## Risks / Trade-offs

- [模型不配合引用约定 → 引用率信号虚高] → D5 指令注入 + 引用率本身就是要度量的对象；Stage 2 judge 兜底"无引用 claim"。
- [标记 token 开销（约 150–240 tokens/大结果）] → min-size 阈值（默认约 200 字符以下不标记）+ per-node 可关闭；开销随启用面线性可控。
- [标记干扰 provider 对 tool content 的解析] → 仅 str content 被标记，原始内容保留可取；`【】`为普通 Unicode 字符，无控制语义。
- [重启后注册表短暂不完整] → 事件重放重建 + unresolved 降级语义（spec 已定），不阻塞调用。
- [引用率 ≠ 准确率，可能给出虚假安全感] → Stage 1 信号明确命名为 uncited-ratio（引用缺失率），UI 与文档不得表述为"幻觉率"；Stage 2 补齐准确性。
- [事实性句段判定为启发式（排除疑问句/短句/代码块）] → 保守启发式，偏差只影响风险信号的分母，不影响引用映射本身；精确判定属 Stage 2 judge。

## Migration Plan

Additive、默认关闭：不配置即零行为差异（无标记、无事件、无开销）。启用按 node 粒度灰度。回滚 = 移除配置；已写入历史的既有标记消息仍可读（标记是普通文本），注册表解析随功能下线自然失效。无 schema migration（D3 不新增表）。

### D8: 事实性句段启发式的 Stage 1 规则集
切分按 `。！？!?` 与换行进行（中英文通用）；` ``` ` 围栏代码块与行内反引号内容整体排除，不参与切分。句子计入分母（factual-eligible）需同时满足：(1) 长度 ≥ 15 字符；(2) 非疑问句（以 `?`/`？` 结尾或以疑问词开头：什么/如何/怎么/是否/哪/吗、what/how/why/when/where/which/who）；(3) 非过程性/意图表述（以 请提供/请确认/让我/我将/let me/let's/I'll/I'm going to 开头——agent 的动作意图不是事实主张）；(4) 非纯列表标记、数字或日期行。计为"已引用"的规则：句内含任意 `【N-M】` 标记——配合 D9 的"句尾标注"措辞约定保证命中率。偏差后果限于风险信号的分母（已列 Risks），不影响引用映射本身。

### D9: 引用指令的措辞与注入触发
- **标记语义具体化**：`N` = session 内工具结果序号（单调递增），`M` = 该结果内 chunk 序号；二元组即全局唯一 ID，与 spec 的 "never reused" 一致。
- **触发**：会话首次调用前注入一次（跟在首条 user 消息后、持久于历史）；此后每次调用对投影做 presence check（探测指令消息的 `[citation_instructions]` 前缀 tag），原指令消息已掉出投影（被窗口选择丢弃/压缩）时补注一条新副本。这是 D5 未覆盖的真实边界——不补注则长会话后期模型看不到引用约定，配合率会静默衰减。
- **措辞**（英文，对齐 4.13 hint 风格；引用约定与响应语言无关）：
  `[citation_instructions] When you state facts derived from tool results, end the sentence with the supporting chunk marker, e.g. 【2-3】. Cite each factual sentence individually; never invent marker numbers — only use markers present in this conversation. Statements not derived from tool results need no citation.`

## Open Questions

- Stage 2 judge 的后端 seam 细节（NLI 先行 or LLM judge 先行）——独立讨论，不在本 change 范围；当前默认倾向 NLI 先行（便宜后端验证引用准确性、LLM judge 作第二后端），正式定于 Stage 2 change 的 design。
