# Design

## Context

ADR-033 已把 durable compaction 的事件 schema 定为 normative；4.13 已交付瞬态投影半边（`runtime/context_processors.py` 的 chain + `CompressionProcessor` 的 fail-fast seam）。本设计是该 ADR 的实现设计，全部决策建立在一轮代码勘察之上。

代码事实（勘察结论，作为设计前提）：

- messages 是 append 型（TOPIC）channel，`is_evictable()=True`；生产 eviction 零接线（全仓库只有定义没有使用点）；`TopicBehavior.resolve_conflict` 的 str 去重在活路径无调用点（休眠代码）。
- 生产装配不注入 `context_budget`（`resolve_budget` 的消费端存在、生产端缺失，budget 恒为默认 8000）——独立的既有缺口，本变更不修。
- `ModelProvider.max_context` 字段已存在（`models/model_provider.py:51`）。
- `LLM_REQUEST` 事件 payload 已存档完整投影（`workers/llm_worker.py:667`），8.20 的两种回放视图（模型当时看到的 / 完整发生的）基础设施已各就各位。
- `fold_session` 只认 CHANNEL_WRITE/EVICTION/FORK 三种 state-carrying 事件；bookkeeping 事件天然跳过。

## Goals / Non-Goals

**Goals:**

- 长会话的有效 surface 规模有界：resume/重放的 fold 输入 = 摘要 + 尾部，不再随历史单调增长。
- fold 语义零改动：压缩不触碰 channel 状态与 fold 机器（Reading A）。
- 压缩全生命周期在日志中可见、可审计、崩溃安全（孤儿 START 永不伪造成摘要）。

**Non-Goals:**

- conversation store（`RuntimePort.conversation_load/save` 面）——独立持久化面，另立变更。
- 8.20 replay UI——本变更只保数据契约（`shadowed_seqs` 完整保留、原文永久可查）。
- in-loop recall（模型找回被遮蔽原文的工具）——记为 extension point（见 D8）。
- overflow bypass（provider `CONTEXT_WINDOW_EXCEEDED` 甄别 + 强制触发）——现状无任何甄别逻辑，错误分类是 provider 脆弱性工作，follow-up。
- 生产 `context_budget` 注入缺口——follow-up；本变更的容量轴触发不依赖它修复。
- microcompaction（工具结果先行修剪的独立机制）——既有 `ToolResultTruncationProcessor` 已覆盖其语义。

## Decisions

### D1. 物化位置：Reading A（channel 全量 + 账本派生视图），否决 fold 物化

**决策**：channel 永不改写，`fold_session` 零改动；「surface replacement」发生在每次 chain 调用时由账本（`CONTEXT_SURFACE_REPLACED` 事件）派生的临时视图里。

**理由**：(1) 保住 logfold 的核心不变量——live 与 replay 走同一 `ChannelBehavior.write`，两边不可能分歧；(2) 零新增存储机制——唯一新持久化产物是现有日志里的四种事件。

**被否决的 Reading B**（fold 认识 `CONTEXT_SURFACE_REPLACED`，物化删区间插摘要进 channel）：需要给 `ChannelManager` 加对称操作、双路径维护替换语义，live/replay 分歧风险与既有 EVICTION/FORK fold 逻辑纠缠；且 ADR 的措辞（"summary node enters the projected view; originals are never deleted"）本就与 Reading A 相容。

**ADR 澄清 1**：Reading A 下 `CONTEXT_SURFACE_REPLACED` 不再是字面的「surface mutation」事件，而是「投影过滤指令」——账本条目。ADR 正文不动，归档时补一行澄清注记。

### D2. 双轴触发语义：容量轴在 chain 入口，不被预算轴短路

Hecate 存在两条正交的轴：预算轴（projection budget，默认 window/8 量级，服务成本治理与 BUDGET_SNAPSHOT）与容量轴（context window，0.8×window，服务硬容量与持久收缩）。单轴系统（窗口即预算，window−reserve）不存在两轴纠缠的问题；双轴结构必须显式区分评估时机。

**决策**：

- 触发判定在 chain 入口（PRE_STEP waterline）执行，判定对象是有效 surface（见 D3），判定条件 `usage ≥ trigger_ratio × context_window`；
- 该判定**不参与** `when_over_budget` 的 satisfaction 短路——否则默认 budget（8000）下投影永远 ≤8k，`tokens` 永远够不到 0.8×window，surface_replacement 上线即死码（饥饿问题）；
- round_window 等预算轴级别照常服务小预算调用（node 显式 8k 预算不值得动持久压缩），但不得以把投影压到容量线以下来阻止容量轴触发。

**饥饿问题的论证**（为什么必须独立评估）：`RoundWindowProcessor` 是 `when_over_budget`（`context_processors.py:536` 未覆写 run_mode），chain 的门是 `tokens > budget` 才放行后续级；若触发判定挂在 compression 级内部，则 window 每轮把投影压回预算内、compression 永不执行、日志照常膨胀——ADR 要消灭的 O(N) 问题原样保留。

### D3. 触发测量点：有效 surface（影子化后、ladder 前），启发式估算

三个候选：channel 全量（压缩后不下降 → 无限重触发循环）、有效 surface（唯一会被压缩实际缩小的量）、投影后（被预算链永远按住 → 永不触发）。**决策取有效 surface**，且影子化视图与 token 测量**同源**（同一函数产出视图及其 token 数），保证「量到的」和「压掉的」是同一个东西。

**估算器**：触发判定用字符启发式（chars/4）。provider-anchor 估算器覆盖的是实际发出的投影前缀，量有效 surface 系统性偏低；0.8 级粗阈值对 ±20% 估算误差不敏感。**记账**：`threshold_snapshot.tokens`、`COMPACTION_COMPLETED.tokens_before/after` 均以有效 surface 计（战果=工作桌收缩量；量 channel 会记成 0 节省）。

**budget ≪ window 配置下的语义诚实**：生产 budget=8k、window=200k 时触发线在 160k——投影在此之前一直被 window 静默裁剪（运营者自选预算，by design），此时压缩的收益是 fold 成本封顶 + 给投影一个连贯摘要前缀，不是阻止窗口溢出（8k 投影撞不了 200k 窗）。运营者要更早的持久收缩调低 `trigger_ratio` 即可——单一触发公式、一个旋钮。

### D4. 身份空间：channel 序数 + 排他不变量 + 锚点校验（`_seq` 为指定演进）

**决策**：`shadowed_seqs` = messages channel 消息序数（含头含尾）。有效性由三层保证：

1. **排他不变量**：任一节点链含 surface_replacement 且 messages 通道 eviction ≠ `NoEviction` → 装配期 fail-fast（挂载在 composition 层——`WorkflowExecutionService` 同时可见 runtime 构造参数与节点链配置；错误类型复用 `ChainPolicyError` 体系）。eviction 生产零接线、dedup 路径休眠，该不变量今天零成本。
2. **锚点校验**：`CONTEXT_SURFACE_REPLACED` payload 在 ADR 三字段外增加 `start_anchor`/`end_anchor`（区间首尾消息 `json.dumps(sort_keys=True)` 后 sha256）。应用账本条目前先验锚点；失配 → 跳过该条 + warning + 原文放行（fail-open）。失败模式是「暂时多看到原文」而非「看到错位内容」。
3. **自愈**：失配区间原文回流 → 有效 surface 变大 → 触发线再命中 → 以当前序数重压、记正确新账（旧条目被覆盖作废，旧事件留日志保审计）。最坏情况由 TerminationProcessor 与（未来的）overflow bypass 兜底。

**被否决的内联标记**：直接给被遮蔽消息打状态标记（如 `compressed` mark）再由投影过滤——与 Reading A 互斥：打标记即改写 channel 内容，而事件溯源下该改写必须落 `CHANNEL_WRITE` 才能在重放后存活，等于滑回 Reading B。我们的 channel 是「政策性不可变」底物（append 语义、默认不删，但机制上存在改写路径）——锚点校验（ETag/CAS 语义）正是把政策性不可变补强到接近构造性不可变的手段。

**`_seq` 指定演进**：消息写入 channel 时打身份字段（provider shaping 剥离，同 `cache_hint` 待遇）。触发条件 = 第一个需要消息身份的消费者出现（in-loop recall / eviction 共存 / 8.20 精确消息引用，任一）。届时新账用 `_seq`，旧下标式条目靠锚点校验继续生效——锚点即新旧身份空间的兼容层。现在不做的理由：动引擎最热路径 + 全量消息断言测试翻修，违反 one-PR-one-purpose；受益方全是后续变更。

**已知限制**：相邻字节相同消息 + 恰好其间的下标移动可能使锚点误通过——概率可忽略、后果有界（下次压缩前隐藏范围偏移至多若干条）、后续压缩自动纠正。记入 spec 已知限制。

### D5. bracket 锁：前提清单、宽松语义、进程内收紧

**锁的存在前提**（写给未来的裁剪者）：锁的价值以三条同时成立为前提——① surface 会被替换（存在中间态）；② 替换含慢操作（LLM 摘要调用，秒级，可失败）；③ 有多个或可恢复的执行者（并发、crash 后 resume、fork）。Hecate 三条全有（Pregel 并行节点、ADR-030 resume/fork、分布式会话存储规划），故锁必须活在日志里（进程内存锁 crash 即失效，fork 子会话也看不见）。若未来某部署形态三条去其一，锁可随该形态降级为普通 bookkeeping。

**被否决的备选**：单事件原子 compaction（SUMMARY+REPLACED+COMPLETED 合一 append）——天然免锁，但丢失进行中可见性（慢 LLM 调用期间日志沉默）、失败尝试留痕、fork 的 open-bracket 语义；那是「个人工具」的选择，Hecate 的可审计定位选 bracket。

**原子性基调：宽松语义为基准 + 进程内锁收紧，跨进程严格化推迟**：

- 宽松基准：重复 START 无害——`compaction_id` 幂等，重叠区间按「后记区间覆盖先记的」消解。该消解逻辑与 D6 rolling 再压缩共用同一套区间覆盖语义，不是妥协而是复用；
- 进程内收紧：runtime 侧每会话一把 `asyncio.Lock`（或 check 与 append START 间不让出事件循环），把单进程竞态窗口（并行 superstep 两节点同过 0.8 线）归零；
- 跨进程（多 runtime 共享会话）：留待分布式会话存储变更做 DB 级条件 append（advisory lock / 唯一部分索引），本变更 design 记载为已知边界。锁的核心价值（crash 可见、resume 语义、审计）声明在日志里，不受此窗口影响。

**stale 判定**：「START 之后出现更新的 `TURN_END` 且无 COMPLETED」= stale（`TURN_END` 由 `pregel.py` 在 graph_complete/interrupt 两种 reason 下发射）。

### D6. 再压缩：rolling + 区间覆盖

第二次压缩的摘要输入 = 上次摘要节点 + 其后新消息。新区间按 channel 坐标（Reading A 下序数稳定）覆盖旧区间重叠部分；旧 `COMPACTION_*` 事件留日志成审计链；`COMPACTION_SUMMARY` payload 记 `prior_compaction_id`。账本应用：条目按日志序应用，后记区间优先于先记的重叠部分——与 D5 宽松语义同一套代码。

### D7. summarizer seam 与 QA 门槛

**runtime 侧**：`CompactionSummarizer` ABC（runtime-internal extension point 命名规范：plain noun，无 Port/Base 后缀），经 `execution_context` 注入（对称于 `context_offloader` 的既有模式）。接口签名只认「消息列表 → 结构化摘要节点」，失败语义由 chain 侧 FailurePolicy 罩住（cooldown/breaker/anti-thrash 已覆盖「LLM-backed processor (e.g. compression summarizer)」——docstring 早已预告）。

**composition 侧**：生产实现走 `RuntimePort.llm_invoke`；ADR 的「routing-pinned summarizer route」「与租户主 cache namespace 隔离」「逐字回放热前缀复用 KV cache」全部是 adapter 的实现细节，**路由概念不泄进 runtime**。

**QA 门槛（最低配）**：拒绝不缩小的摘要 + 结构校验（结构化摘要节点的必需字段）。校验失败 = 压缩未发生——surface 原样、流程落到既有 TerminationProcessor 兜底，与 D5 的崩溃语义闭环（失败尝试仍落日志留审计）。

**ADR 澄清 2**：payload 扩展（`start_anchor`/`end_anchor`/`prior_compaction_id`、`threshold_snapshot.tokens` 的有效 surface 定义）是 ADR 事件 schema 的 additive 细化，归档时随澄清 1 一并补注。

### D8. in-loop recall 的 extension point 预留

不做实现。预留方式：账本条目已含找回所需的全部信息（`shadowed_seqs` + 日志原文永久可查）；未来 recall 工具 = 读账本 + 按 seq 取原文，即 D4 的 `_seq` 演进触发条件之一。`1.3.15b` offload 仍是压缩下层的可恢复 tier（offload 先于 shadowing 发生）。

### D9. window 解析与配置面

优先级：`ModelProvider.max_context`（composition 注入 `execution_context["context_window"]`）→ `context_policy.py` 的 `_WINDOW_HINTS` 表兜底（新增 `model_default_window()`，复用既有表）→ 均无法解析且 backend=surface_replacement → 装配期 fail-fast（没有窗口算不出触发线）。配置面：`_PARAM_SPECS["compression"]` 增加 `trigger_ratio`（默认 0.8）、`retain_ratio`（默认 0.16），与既有 `backend` 参数同套 load-time 校验。

### D10. 挂载位置与 checkpoint 语义

**影子化视图在 `ContextProcessorChain.apply` 入口的公共路径**（不从节点策略分支），读取靠 `execution_context` 的 `event_store` + `session_id`（chain.apply 已持有）。节点 backend 配置只决定「谁有资格触发」，不决定「是否应用已有账本」——否则同会话两种投影并存且互相矛盾。

**checkpoint 弱解释**：`COMPACTION_COMPLETED` 是 checkpoint source 的落法 = 紧随其后的既有 superstep checkpoint 天然覆盖 bracket（conversational 模式每 superstep 落 checkpoint，`pregel.py:981`）；本变更只做两件事——checkpoint metadata 记 `last_compaction_id`（一行）、resume 测试断言恢复后投影应用账本。**不做**日志前缀跳跃（「从 COMPLETED 起折」对多 channel 会话不成立——其他 channel 状态在前缀里）；真正的收益由「后续 fold 输入=摘要+尾部」在投影侧兑现。

**与 `LLM_REQUEST` 底档的协同**：日志已按调用存档完整投影，故 8.20 的 shadowing-on 视图=直接读 `LLM_REQUEST.payload.messages`（压缩后事件天然是摘要+尾部投影，零新代码），shadowing-off 视图=Reading A 的 channel fold 本身（零新代码）。

### D11. 事件序列化纪律

SUMMARY payload 剥离超大载荷（image 等大二进制、超长 content 走有界保留——`LLM_REQUEST` 既有 `_bounded_retainer` 模式可复用）。替换载荷若残留大对象，会诱发后续反复重压缩；且日志本就每调用带一份投影拷贝，SUMMARY 再带大 payload 属雪上加霜。

## Risks / Trade-offs

- [摘要质量有损] → retain_ratio 0.16 尾部逐字保留 + offload 下层可恢复 + 结构化摘要模板（objective/decisions/state/next steps）；QA 门槛拒绝劣化摘要。
- [锚点校验的自愈路径产生额外压缩] → 有 FailurePolicy anti-thrash 罩住（低收益+少新消息则跳过）；自愈压缩记新账后锚点恢复匹配，路径收敛。
- [多进程共享会话时锁窗口存在] → 宽松语义保证最坏结果是重复摘要成本而非投影腐化；严格化随分布式会话存储变更交付（D5）。
- [budget 注入缺口让本变更收益打折（budget=8k 下投影仍被裁剪）] → 容量轴独立评估已消除对压缩的阻断；budget 注入 follow-up 补齐后收益完整体现；缺口已文档化。
- [既有 spec 的小写事件名与 ADR EventType 不一致] → delta 统一为 ADR 命名（ADR normative），归档时主 spec 自然换新。
- [channel 序数依赖排他不变量，未来误启用 eviction] → 装配期 fail-fast + 锚点校验 fail-open 双保险；`_seq` 演进是终解。

## Migration Plan

纯 additive：四个 EventType 对旧读者走 CUSTOM 回退（ADR-030 §1 既有契约，持久化适配器侧需测试验证）；fold 对压缩事件零感知；默认链行为不变（backend 默认 projection）。无数据迁移（`max_context` 字段已存在）。回滚 = 不配置 surface_replacement backend，一切回到 4.13 现状。

## Open Questions

（无——探索阶段 B1-B7 已全部定案；overflow bypass 与 budget 注入为已文档化的 follow-up，不影响本变更的 spec 与任务结构。）
