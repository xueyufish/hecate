# Design — Grounding Scoring Layer (1.3.5e Stage 2)

## Context

Stage 1 已交付溯源层(`runtime/citation_provenance.py`:registry / 回映射 / D8 事实句 / 引用率信号),打分层需要的 grounding source 与 claim 清单都已存在且经事件可重建。行为契约见 specs(grounding-scoring + citation-provenance / agent-node-config delta)。

一个本 design 必须处理的现状:`llm_worker.py` 两条执行路径只调用 legacy `_pre_hook`/`_post_hook`,`middleware_chains` 存而未跑(`ToolWorker` 已是 chain 形状,LLMWorker 落后一步)。Stage 1 design D4 预留的「judge stage group 迁移」以接线为前置。

## Goals / Non-Goals

Goals:
- 纯观察的打分面:claim → 证据(查表 + 兜底检索)→ 三态裁决 → `GROUNDING_SCORE` 事件(含 `would_block` 影子判定),默认关闭。
- 后端 seam 稳定:LLM judge(day-1 可用)与 HTTP NLI(MiniCheck 类自托管即插即用)两种后端形态,escalation 升级路径。
- 补齐 LLMWorker 的 `LLM_RESPONSE` chain 执行(既有 hook 语义等价迁移)。
- studio 引用徽标(Stage 1 承诺)。

Non-Goals:
- 处置动作与用户可见反馈(block / regenerate / warn 升级 / 高亮)——Stage 3,以 `would_block` 数据校准后立项。
- 原生引用输入形态(Anthropic Citations 等)、Judge & Jury、ADR-033 compaction 接地校验。
- atomic-claim 细粒度拆分(D8 句子是 v1 契约,见 D4)。
- 打分结果的会话内重建(事件只作审计与校准数据源,不参与 fold)。

## Decisions

### D1: 后端 seam 为 runtime 内部扩展点(裸名词 ABC),两实现共用同一裁决契约
`GroundingScorer` ABC:`score(pairs: list[ClaimEvidencePair]) -> list[ScoringResult]`,结果含三态裁决 + 置信度 + degradation 标记。`LlmJudgeScorer` 走 `RuntimePort.llm_invoke`;`HttpNliScorer` 走可配置 HTTP 端点。Alternatives:挂 PostLLMHook(`GuardrailResult` 无元数据旁路,Stage 1 D4 已否决);写死 LLM judge(锁死后端,放弃调研确认的业界主流 NLI 路径)。契约(而非实现)是稳定面——后端增减不改 runtime 其余部分。

### D2: LLM judge 批量判定(一次调用判全部对),格式解析失败按 degradation 降级
一个响应的全部 (claim, evidence) 对合并为一次 judge 调用,要求结构化返回逐对裁决。Alternatives:逐对调用(噪声低但成本/延迟随对数线性爆炸,10 句 3 引即 ~30 对)。批量调用失败重试一次,再失败按对记录 degradation。judge 模型复用节点的模型配置(policy 可覆盖模型名),不新建模型路由体系——与 1.3.18 evaluator 模型分离的先例对齐,专用 scoring 路由留待模型面扩展。

### D3: HTTP NLI 端点契约支持三态与二分类两种形态
请求:`{pairs: [{claim, evidence}]}`;响应:`{results: [{verdict, confidence}]}`,`verdict ∈ supported|contradicted|unverifiable`,或简化形态 `{support: bool}`(平台映射:false → `unverifiable`,true+低置信 → `supported` 低置信;二分类端点永不产生 `contradicted`)。超时按对预算(默认 ~500ms/对,可配),超时按 degradation。Alternatives:平台内嵌 transformers 推理(破坏 model-agnostic 与部署形态,否;调研中 NLI 自托管是唯一新增运维组件,HTTP 形状把它留给企业侧)。

### D4: claim 清单复用 D8 事实句;矛盾轴按后端契约定义,红线写进映射规则
claim = `factual_sentences()` 输出(与引用率信号同源,事件分母一致);句内标记经正则关联到 `CitationMap`(新增一个句级标记提取的纯函数,不改 Stage 1 语义)。`contradicted` 只能来自后端显式 disagreement:LLM judge prompt 三选一;HTTP NLI 二分类映射规则中不存在矛盾出口。低置信一律映射 `supported` 低置信(Vertex `confidenceScore` 语义混淆的教训,spec 已立红线)。

### D5: 兜底检索用独立 `fallback_kb_ids`,不复用节点 `kb_ids`
未引用句经 `RuntimePort.knowledge_query` 检索(默认 top_k=3,policy 可调),检索上下文整体作为该句证据。Alternatives:复用节点检索 `kb_ids`(把打分行为隐式耦合到节点检索配置——改检索配置会静默改变打分行为,拒)。无 fallback 配置时未引用句直接 `unverifiable`,零额外调用。

### D6: LLMWorker 补齐 `LLM_RESPONSE` chain 执行,既有 hook 语义等价迁移
两条执行路径改为取 `middleware_chains[Phase.LLM_RESPONSE]` 执行;legacy `post_llm_hook` 经既有 adapter 作为链内 stage,terminal handler 保持现有响应处理——BLOCK 替换罐头消息 / SANITIZE 替换 content 的语义与事件路径不变,现有 guardrail/security 测试必须原样通过。打分注册为该链的第一个真实业务 stage:返回 ALLOW、不短路,裁决写响应元数据旁路(与 `chain_report` 同模式)进 `GROUNDING_SCORE` 事件。Alternatives:继续 worker 内部直调 scoring(少接线,但 D4 前景下 Stage 3 的 BLOCK 需要 stage 身份与 decision-sink 审计,届时再迁等于两次动安全路径;且 ToolWorker 已是 chain 形状,LLMWorker 补齐是还债不是创新)。风险对冲见 Risks。

### D7: 触发与预算:`on_uncited` 默认,`always` 与采样互斥
`trigger: on_uncited(default) | always | sample` + `sample_rate: float`。`on_uncited` 下零引用缺失的响应零开销(命中绝大多数健康响应)。非流式打分计入响应延迟(预算:NLI ≤ ~200ms / judge 秒级);流式后置执行,token 交付不受影响(行业默认,调研 §5)。scoring 阶段任何失败降级为无裁决,不阻塞交付。

### D8: `GROUNDING_SCORE` 事件 payload 对齐 `CITATION_*`;`would_block` 阈值先做代码常量
Payload:`{event_name, per_claim: [{sentence, verdict, confidence, evidence: {kind: cited|fallback|none, ref, truncated}, degraded}], aggregates: {supported/contradicted/unverifiable counts+ratios}, would_block: {triggered, reason}}`。`would_block` 按 Stage 3 起草阈值计算(任一 contradicted 或 contradicted_ratio/unsupported_ratio 超预设),阈值作为代码常量并注释"Stage 3 policy 化"——不进用户 policy,避免"看起来能配处置"的误导。发射失败仅告警(与 `CITATION_*` 同契约)。

### D9: 策略独立成 `grounding_policy.py`,字段集最小完整
`{enabled, backend(llm_judge|http_nli + endpoint), trigger, sample_rate, fallback{enabled, kb_ids, top_k}, escalation{enabled, max_pairs}}`,fail-fast 校验 + canonical hash,对齐 `provenance_policy.py`(D7 先例:单一职责,独立模块)。escalation v1 行为:`http_nli` 后端下,置信度低于阈值的对升级一次 `LlmJudgeScorer`(上限 `max_pairs`);`llm_judge` 为主后端时 escalation 无操作。

### D10: studio 徽标只读 `citations` 元数据,不读打分
assistant 消息的 `citations` key(Stage 1 已写)即数据源:cited/unresolved 计数徽标 + marker 明细,unresolved 视觉区分。打分高亮明确留给 Stage 3(裁决语义定了才有高亮语义)。

## Risks / Trade-offs

- [LLM judge 误判 contradicted,制造虚假告警] → 红线映射(D4)+ contradicted 仅落事件不动作;Stage 3 立项前用 `would_block` 真实数据测冤枉率。
- [chain 接线回归破坏既有 guardrail 行为] → 行为等价是硬验收:现有 guardrail/security 测试原样通过 + 新增等价性测试(BLOCK/SANITIZE/事件路径逐项);上线顺序 = 先接线后开 policy。
- [批量 judge 输出格式不稳] → 结构化解析失败重试一次,再失败按 degradation 记录(响应不受影响)。
- [兜底检索增加延迟与 KB 负载] → 仅 `on_uncited` 触发的未引用句才检索 + top_k 小 + 端到端超时降级。
- [HTTP NLI 端点慢/不可用] → 按对超时 → degradation;端点是企业自配资产,平台不做健康管理(v1)。
- [`always` 触发的成本失控] → 文档红线(judge 调用按对计费)+ `sample_rate`;默认 `on_uncited`。
- [evidence 文本长度膨胀 judge prompt] → chunk 粒度由 Stage 1 policy 限定(默认 ~300 字符),无需额外截断。

## Migration Plan

Additive、默认关闭:不配置 `grounding_scoring` 即零行为差异(无打分、无 judge 调用、无新事件)。分两步上线:(1) chain 接线合并——行为等价,scoring 不可用;(2) policy 面开放——按节点灰度。回滚:策略还原默认(接线保留,它本身是正确性修复);已产生的 `GROUNDING_SCORE` 事件为 additive 记录,无 schema 迁移。studio 徽标随前端发布独立灰度。

## Open Questions

- Stage 3 处置阈值与 `would_block` 的默认值校准——关闭路径:灰度期从 EventStore 查询 `would_block` 命中样本,经 7.4 人工标注回流标注「真幻觉 / 冤枉」得冤枉率,反向抽样未命中响应测漏报率;Stage 3 proposal 携带这组数据定阈值后关闭。测量工具(查询样例与标注流程)见 tasks 7.3 校准 runbook。
