# Grounding Scoring Layer (1.3.5e Stage 2)

## Why

Stage 1 溯源层回答了「引用了什么」:claim-to-chunk 引用映射与引用率 warn 信号已落事件。但没有回答「引用得对不对」——蕴含从未校验;未引用的事实句没有证据可依;引用率只是打分层最便宜的代理信号。2026-09 业界调研(见 [docs/research/2026-09-hallucination-grounding-survey.md](../../../docs/research/2026-09-hallucination-grounding-survey.md)):运行时 grounding 打分在企业云平台层已被验证为产品能力(Bedrock contextual grounding 双分 + 阈值 + BLOCK、Azure groundedness 三态),但全部 20 个调研样本无一交付完整的「claim → 证据 → 打分 → 动作」管线——测量侧需求已被相邻市场(Ragas / Galileo / Patronus)反复验证,动作端的价值工作点(假阳性经济学)连 AWS 都以"先 NONE 模式调阈值"处理。

因此本 change 交付**打分面且仅打分面**:纯观察、不拦截、不改写。处置动作(Stage 3)不在本 change——它将由本 change 产出的真实打分数据驱动立项:打分事件携带 `would_block` 影子判定(按 Stage 3 预设阈值),灰度期直接抽审影子命中的假阳性率,把 Stage 3 从设计假设变成测量驱动的 rollout。调研同时确认:后端业界主流是「NLI 小模型主路径(MiniCheck-FT5,~50–100ms/对,GPT-4 级准确率 @ ~400× 低成本)+ 边界 case 升级 LLM judge」,且 MiniCheck 的 `(claim, document)` 输入形状与 Stage 1 chunk 注册表直接对接。

## What Changes

- **`GroundingScorer` 扩展点(裸名词 ABC)+ 双后端**:`LlmJudgeScorer`(走 `RuntimePort.llm_invoke`,任何部署形态 day-1 可用)与 `HttpNliScorer`(自定义 HTTP 端点契约——企业自托管 MiniCheck-FT5 / HHEM-2.1-Open 后填 URL 即接入,平台不内嵌模型运行时);后端按策略选择,policy 预留升级(escalation)字段(NLI 主路径 → judge 边界升级)。
- **claim 清单复用 Stage 1 D8 确定性事实句**:已引用句 → `(句, 已引 chunk 文本)` 对直接查表打分;未引用句 → `RuntimePort.knowledge_query` 兜底检索(独立 `fallback_kb_ids` 字段,不复用节点 `kb_ids`)取得证据后再打分;未配置 KB 或检索为空 → `unverifiable`。
- **三态裁决**:每句 `supported` / `contradicted` / `unverifiable` + 置信度;矛盾轴由后端契约定义(LLM judge prompt 三选一;HTTP NLI 端点按响应契约映射)。明确红线:低置信度不得映射为 `contradicted`(Vertex confidenceScore 教训)。
- **LLM_RESPONSE 链接线补齐**:LLMWorker 现状只执行 legacy hook(`_pre_hook`/`_post_hook`),`middleware_chains` 存而未跑;本 change 补上 `Phase.LLM_RESPONSE` 链执行,打分作为第一个真实 chain stage(Stage 1 design D4 预留路线),获得 stage 身份审计与 BLOCK 短路语义(本 change 内 BLOCK 不会被触发——观察语义)。
- **同步预算与触发条件**:打分在 worker 响应路径内执行(非流式增加响应延迟;流式路径后置执行,不影响已交付 token——行业默认是后置拦截)。默认仅 `uncited_ratio > 0` 时打分,可选 `always` / 采样;预算目标 NLI ≤ ~200ms、judge 升级秒级。
- **新审计事件 `GROUNDING_SCORE`**(additive CUSTOM,对齐 `CITATION_*` / `BUDGET_SNAPSHOT` 模式):逐句裁决、置信度、证据来源(cited chunk / fallback chunk / none)、聚合指标、`would_block` 影子判定。
- **per-node 策略 `grounding_scoring`**(`citation_provenance` 的兄弟键):enabled(默认关)、backend、trigger、thresholds、fallback(enabled + `fallback_kb_ids`)、escalation;fail-fast 校验与 canonical hash 并入 agent 版本化,语义对齐 `provenance_policy.py` 先例。
- **studio 引用徽标**:兑现 Stage 1 proposal 承诺——citation map(`【N-M】` cited/unresolved)在 studio 会话/回放视图可视化。纯只读展示,不含打分高亮。
- **非目标(留待后续 change)**:处置动作 pass/warn 升级/block/regenerate(Stage 3,数据驱动);模型原生引用输入形态(Anthropic Citations 等自报引用接入校验);Judge & Jury 多裁判投票;ADR-033 compaction 接地校验;打分高亮 UI。

## Capabilities

### New Capabilities

- `grounding-scoring`: 运行时 grounding 打分面——claim 清单(D8 事实句)、证据获取(引用查表 + `knowledge_query` 兜底)、`GroundingScorer` 后端 seam(LLM judge / HTTP NLI)、三态裁决与置信度、`GROUNDING_SCORE` 审计事件(含 `would_block` 影子判定)、per-node 策略(默认关闭)、流式/非流式路径语义,以及「纯观察」行为契约(不拦截、不改写、不重排交付)。

### Modified Capabilities

- `citation-provenance`: 新增引用徽标可视化要求——assistant 消息的 citations 元数据在 studio 会话/回放视图中以徽标形式可查(Stage 1 承诺的 studio 侧交付);打分不在该 capability 范围内。
- `agent-node-config`: 新增 `grounding_scoring` 按节点策略配置需求(enabled / backend / trigger / thresholds / fallback / escalation),校验语义对齐既有 citation provenance 配置需求。

## Impact

- **代码**:`src/hecate/runtime/`——新增 `grounding_scoring.py`(scorer seam、双后端、裁决聚合)与 `grounding_policy.py`(策略解析,对齐 `provenance_policy.py`);`middleware.py` / `middleware_factory.py` / `security/guardrail_assembly.py`(LLM_RESPONSE 链组装);`workers/llm_worker.py`(chain 接线,替换 legacy-only 路径);`citation_provenance.py`(claim 清单与证据查表复用,预期只读复用不改语义)。`src/hecate/studio/` 引用徽标展示。
- **事件**:新增 `GROUNDING_SCORE`(additive CUSTOM payload);EventStore 消费者兼容性同 Stage 1 的 `CITATION_*`。
- **性能**:打分为响应路径内的同步步骤,开销 = 触发条件 × 后端延迟(NLI ≤ ~200ms / judge 秒级);默认 `on_uncited` 触发把覆盖面压到有引用缺失的响应;打分失败降级为无裁决(仅告警),不阻塞交付。
- **成本**:LLM judge 调用按既有模型调用记账;NLI 后端成本在企业侧(HTTP 端点),平台不计费。
- **API**:无 breaking;节点配置面新增字段(canonical hash 变化仅对显式启用者生效);studio 只读。
- **依赖**:无新增 Python 包(NLI 经 HTTP 端点接入,不内嵌推理运行时)。
