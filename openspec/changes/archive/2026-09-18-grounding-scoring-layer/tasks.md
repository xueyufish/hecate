# Tasks — Grounding Scoring Layer (1.3.5e Stage 2)

## 1. 后端 seam 与裁决契约

- [x] 1.1 新建 `runtime/grounding_scoring.py`:`ClaimEvidencePair` / `ScoringResult`(三态 verdict + confidence + degradation)与 `GroundingScorer` ABC(裸名词扩展点,批量 `score(pairs)`);三态红线映射(低置信 → supported 低置信)写在结果构造处
- [x] 1.2 `LlmJudgeScorer`:批量判定实现——全部对合并一次 `RuntimePort.llm_invoke` 调用(prompt 三选一契约),结构化解析失败重试一次后按对降级;模型取 policy 覆盖或节点模型配置
- [x] 1.3 `HttpNliScorer`:HTTP 端点契约实现(D3:三态 + 二分类两种响应形态映射,二分类无 contradicted 出口),按对超时、超时/错误按对降级
- [x] 1.4 escalation:`http_nli` 后端下低置信对升级一次 `LlmJudgeScorer`(`max_pairs` 上限);`llm_judge` 主后端时无操作

## 2. 证据获取与裁决聚合

- [x] 2.1 claim 清单接线:复用 `factual_sentences()`;新增句级标记提取纯函数,关联 `CitationMap` 得到 (sentence → cited markers);经 `CitationRegistry` 查表取 chunk 文本(truncated 状态保留)
- [x] 2.2 兜底检索:未引用句经 `RuntimePort.knowledge_query` 检索(`fallback_kb_ids` 独立字段,top_k 默认 3);无配置/空结果 → `unverifiable`;fallback 证据在结果中区分 `kind`
- [x] 2.3 响应级聚合:supported/contradicted/unverifiable 计数与比率;`would_block` 影子判定(代码常量阈值 + "Stage 3 policy 化"注释)

## 3. LLM_RESPONSE 链接线与打分 stage

- [x] 3.1 LLMWorker 两条执行路径迁移为执行 `middleware_chains[Phase.LLM_RESPONSE]`:legacy `post_llm_hook` 经 adapter 入链、terminal handler 保持现有响应处理;BLOCK/SANITIZE 语义与事件路径不变
- [x] 3.2 打分注册为 `LLM_RESPONSE` chain stage:返回 ALLOW 不短路,裁决经响应元数据旁路传出;观察语义(不改写、不拦截、流式后置)
- [x] 3.3 触发条件执行:`on_uncited`(默认)/`always`/`sample` + `sample_rate`;未命中触发零调用零事件;非流式计入响应延迟、流式交付后执行

## 4. 事件与策略

- [x] 4.1 `GROUNDING_SCORE` 事件发射(D8 payload:per_claim + aggregates + would_block),对齐 `CITATION_*` / `emit_citation_event` 的 best-effort 契约;发射失败仅告警
- [x] 4.2 新建 `runtime/grounding_policy.py`:`resolve_grounding_policy(node_config, agent_policy)` —— D9 字段集 fail-fast 校验、默认关闭、canonical hash 并入节点策略 hash
- [x] 4.3 节点配置面(agent-node-config)暴露 `grounding_scoring` 策略字段;`WorkflowExecutionService`/`PregelRuntime` 装配路径透传 resolved policy(对齐 4.13/citation passthrough 模式)

## 5. studio 引用徽标

- [x] 5.1 会话/回放视图中 assistant 消息的 citation badge:cited/unresolved 计数 + marker 明细,unresolved 视觉区分,无 citations 不渲染;只读 `citations` 元数据

## 6. 测试与验证

- [x] 6.1 `tests/test_runtime/test_grounding_scoring.py`:scorer 契约(三态映射红线/批量解析失败降级/HTTP 二分类映射/超时降级/escalation 上限);证据获取(查表/兜底/无配置 unverifiable/truncated 保留);聚合与 would_block
- [x] 6.2 链接线行为等价测试:现有 guardrail/security 测试原样通过;新增 BLOCK/SANITIZE/事件路径等价性用例(legacy hook 迁移前后行为逐项一致);scoring stage 永不短路
- [x] 6.3 策略解析测试:默认关闭端到端零行为差异(无打分/judge 调用/事件)、fail-fast 校验、canonical hash 稳定性、escalation 字段校验
- [x] 6.4 投影交互测试:offload/recall/truncation 后证据仍可解析(对齐 Stage 1 交互不变量)
- [x] 6.5 全量验证:`ruff check` + `ruff format --check` + `mypy` + `pytest tests/ -q` 四项 0 错误

## 7. 文档

- [x] 7.1 `docs/features/feature-catalog.md` / `docs/features/roadmap.md`:1.3.5e 标注 Stage 2 交付范围(打分面 + 双后端 seam + 兜底检索 + 影子判定),Stage 3(处置动作)保持 planned 并注明立项前置 = 经 7.3 runbook 校准的冤枉率/漏报率数据
- [x] 7.2 `docs/gotchas.md`:补充「打分是 heuristic 不是 trust boundary」「低置信 ≠ contradicted」「would_block 无行为效果」三条红线;HTTP NLI 端点契约要点
- [x] 7.3 校准 runbook:`GROUNDING_SCORE` 事件的 EventStore 查询样例(`would_block` 命中/未命中抽样)+ 冤枉率、漏报率的人工标注与统计流程(对接 7.4 标注回流),作为 Stage 3 立项的直接输入
- [x] 7.4 HTTP NLI 端点契约 schema 集成文档(D3 的请求/响应形态、verdict 映射规则、超时与降级语义),供企业自托管 MiniCheck 类后端接入
