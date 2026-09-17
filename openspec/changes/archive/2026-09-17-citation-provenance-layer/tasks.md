# Tasks — Citation Provenance Layer (1.3.5e Stage 1)

## 1. 注册表与标记器

- [x] 1.1 新建 `runtime/citation_provenance.py`：`CitationRegistry`（session 级、append-only、ID 永不复用、`resolve()` 跨投影状态可解析、`mark_truncated()` partial 记录）与 `ChunkMarker`（str content 确定性切分 + `【N-M】` 前缀注入，N=session 内结果序号、M=结果内 chunk 序号；min-size 以下原样通过）
- [x] 1.2 `CitationBackMapper`：响应文本 `【N-M】` 正则提取 → 引用映射（已注册/未解析两类），确定性、无模型调用
- [x] 1.3 `UncitedRatioSignal`：保守启发式句段切分（排除疑问句/短句/代码块）→ 无引用句占比计算
- [x] 1.4 注册表事件重建：`CITATION_REGISTERED`/`CITATION_MAP`/`CITATION_RISK` payload 定义（对齐 BUDGET_SNAPSHOT 模式）+ 会话重启后事件重放 `rebuild_from_events()`；重建前 resolve 记为 unresolved

## 2. 工具结果写入路径

- [x] 2.1 tool worker 工具结果写入处接线 `ChunkMarker`：仅对 `citation_provenance` 已启用节点生效；原始 unmarked content 保留在消息记录；非 str content 原样通过
- [x] 2.2 chunk 注册与 `CITATION_REGISTERED` 事件发射（emission 失败仅告警不影响调用）；注册表生命周期对齐 `ContextChainFactory` 的 per-session 缓存模式

## 3. 引用指令注入

- [x] 3.1 引用约定指令注入（D9）：会话首次调用前注入一次 user-role 指令（`[citation_instructions]` 前缀 tag，不改 system prompt）；此后每次调用对投影做 presence check，指令掉出投影时补注新副本；配置开关控制

## 4. 回映射与风险信号

- [x] 4.1 LLMWorker 响应路径接线 `CitationBackMapper` + `UncitedRatioSignal`：引用映射写入响应消息元数据；接口形状按未来 middleware `LLM_RESPONSE` stage 设计（D4）
- [x] 4.2 `CITATION_MAP`/`CITATION_RISK` 事件发射（引用映射、unresolved 引用数、uncited ratio、warn level）；纯观察语义——不拦截、不改写、不延迟响应

## 5. 策略解析与节点配置

- [x] 5.1 新建 `runtime/provenance_policy.py`：`resolve_citation_policy(node_config)` —— enable flag、chunk granularity、min-size threshold；fail-fast 校验（未知字段/非法值报错指明字段）；默认关闭；canonical hash 贡献并入节点策略 hash
- [x] 5.2 节点配置面（agent-node-config）暴露 `citation_provenance` 策略字段，校验语义与 5.1 一致
- [x] 5.3 `WorkflowExecutionService`/`PregelRuntime` 装配路径透传 resolved policy 与 registry（对齐 4.13 `context_chain` passthrough 模式）

## 6. 与 4.13 链的交互验证

- [x] 6.1 截断交互测试：`tool_result_truncation` 截断 marked chunk 后头部标记存活、注册表 partial 状态正确（无需修改 4.13 代码，验证既有尾部语义兼容）
- [x] 6.2 offload/recall 交互测试：offload 后 registry resolve 不失效；recall 恢复内容携带原标记、无重编号

## 7. 测试与验证

- [x] 7.1 `tests/test_runtime/test_citation_provenance.py`：标记器（切分边界/min-size/非 str）、注册表（ID 不复用/跨轮 resolve/partial/重建）、回映射（多引用/未知标记）、风险信号（启发式边界/ratio 计算）
- [x] 7.2 策略解析测试：默认关闭、fail-fast 校验、canonical hash 稳定性；默认关闭节点端到端零行为差异（无标记/无事件/无开销）
- [x] 7.3 全量验证：`ruff check` + `ruff format --check` + `mypy` + `pytest tests/ -q` 四项 0 错误

## 8. 文档

- [x] 8.1 `docs/features/feature-catalog.md` / `docs/features/roadmap.md`：1.3.5e 标注 Stage 1 交付范围（溯源层 + 引用率信号），Stage 2/3（judge/处置/knowledge_query 兜底）保持 planned
- [x] 8.2 `docs/gotchas.md`：补充"标记写入 channel state、原始内容保留"的数据形态说明与"引用率 ≠ 幻觉率"的表述红线
