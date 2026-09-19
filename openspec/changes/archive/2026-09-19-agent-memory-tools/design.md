# Design

## Context

四层记忆底座已就位(L1 `MemoryBlockModel` / L2 压缩 + ADR-033 compaction / L3 `MemoryModel` / L4 `KnowledgeMemoryModel` + Qdrant hybrid),对话底座为事件溯源(`events` 表,CHANNEL_WRITE 携带全部消息,`event_retention_service` 会清理)。`MemoryProvider` 接缝(`core/composition/memory_provider.py`)目前只有 `search()`,单后端选择、module 级缓存、失败降级 None。工具面:内置工具经 `BUILTIN_TOOL_DEFINITIONS`(JSON Schema + `BuiltInToolExecutor.execute(name, args, context)`)定义与执行,`seed_builtin_tools()` 写入 DB 工具注册表,Agent 按名称挂载。上下文工程:4.13 `ContextProcessorChain`(processor ABC:`run_mode` = always / when_over_budget;默认链 truncation → KV guard → window → offload → compression → termination;`ChainContext.execution_context` 可跨层传执行上下文)。动机与范围见 proposal.md,行为契约见三个 spec delta。

## Goals / Non-Goals

**Goals:**

- 工具层与后端解耦:工具 executor 面向 tier 契约编程,hecate-memory 只是第一个实现。
- 8 个记忆工具 + 会话召回 + prefetch 全部 flag 默认关,关闭路径零行为差异。
- 所有记忆写入可审计(`memory_edit_log`)、可并发保护(`revision`)、租户隔离(workspace 一等公民)。
- 复用既有机制:seeding 通路、embedding_service + VectorStore 抽象、ContextProcessorChain、4.13 失败策略。

**Non-Goals:**

- L3 ADD-only supersession / temporal 列(与 3.5.13 合流时做)。
- studio UI(Memory Center、召回浏览器)。
- 跨 Agent 检索、抽取级召回(Google/AWS 形态)、OpenClaw 式 recall sub-agent(4.20 v2)。
- 第三方 provider 的具体适配器(mem0/zep)——本期只交付契约 + builtin 实现。
- L2 压缩与 compaction 的任何改动。

## Decisions

### D1. Provider 契约:Protocol + 能力声明,非 ABC 继承墙

沿用现有 duck-typing(`MemoryProvider` Protocol),扩展方法集并新增 `capabilities()` 声明(返回支持的方法/ tier 集合)。组合根按声明路由;调用未声明能力返回结构化错误对象(不抛异常穿越工具边界)。选择理由:现有协议即 Protocol;第三方实现者(mem0 SDK 等)无需继承任何基类;与 `auth/resolver.py` 风格一致。备选:ABC + 注册基类——被否,侵入第三方且与现状断裂。

方法集(签名级,最终以实现为准):

```python
class MemoryProvider(Protocol):
    # tier-1(现有,保持)
    async def search(self, collection_name, query, *, limit=10, mode="hybrid",
                     workspace_id=None) -> list[SearchHitLike]: ...
    # tier-2
    async def search_memories(self, *, query, workspace_id, agent_id=None,
                              user_id=None, top_k=5, tags=None) -> list[MemoryFactHit]: ...
    async def search_recall(self, *, query, workspace_id, agent_id, limit=5,
                            start_date=None, end_date=None, roles=None,
                            cursor=None, exclude_session_ids=None) -> RecallPage: ...
    # tier-3
    async def add_memory(self, *, content, workspace_id, agent_id=None,
                         user_id=None, tags=None, importance=0.5) -> MemoryWriteResult: ...
    async def update_memory(self, *, memory_id, workspace_id, patch, expected_revision=None) -> MemoryWriteResult: ...
    async def forget_memory(self, *, memory_id, workspace_id) -> MemoryWriteResult: ...
    # 生命周期
    async def prefetch(self, *, query_text, workspace_id, agent_id=None,
                       user_id=None, max_entries, max_tokens) -> list[PrefetchEntry]: ...
    async def sync_turn(self, *, workspace_id, agent_id, session_id,
                        messages: list[dict]) -> None: ...
    def capabilities(self) -> frozenset[str]: ...   # {"search","search_memories","search_recall","add","update","forget","prefetch","sync_turn"}
```

结构化结果对象(`MemoryWriteResult{ok, memory_id, revision, error}`、`RecallPage{hits, next_cursor}`)承载降级信息,工具层将其翻译为模型可读的错误文本。

### D2. 工具执行路径:扩展 BuiltInToolExecutor + 注入 memory backend

`BuiltInToolExecutor` 增加可选构造参数 `memory_backend`(组合根注入;`None` 时记忆工具 fail-closed 返回带说明的结构化错误,复用 `skill_loader` 的先例)。执行所需身份从 `context` dict 取:`agent_id` / `workspace_id` / `session_id`(load_skill 已传前两者,browser 已传 session_id——本变更加固这三者的必传约定,缺失即结构化报错)。DB 会话由 backend 自持 factory,不经 context 传递。

备选:独立 MemoryToolExecutor——被否,会分裂工具分发与 policy/middleware 管线(记忆工具必须同样过 tool policy pipeline 与 guardrail hooks)。

### D3. L1 编辑实现:字符串精确匹配 + 歧义拒绝(Hermes/Letta 范式)

`memory_replace` 以 `old_string` 在块内容上做精确子串查找:`count==0` 报"未找到",`count>1` 报"歧义(含匹配次数)",只有 `count==1` 执行;空 `new_string` 即删除片段。块 limit 校验在写入前(超限显式报错,含用量/上限,不静默截断——对齐 Claude Code auto memory 的报错式上限)。`memory_rethink` 不做自动建块(与 Letta 不同):块由 REST/模板创建是既有治理面,工具自动建块会绕过 limit/position 治理,故 `label` 不存在一律结构化报错。

### D4. revision 列 + memory_edit_log 表(审计走 DB 表,不加 EventStore 事件)

- 三个记忆表各加 `revision INTEGER NOT NULL DEFAULT 1`;工具修改成功时 `UPDATE ... SET revision = revision + 1 WHERE id=? AND revision=?` 单语句完成乐观并发校验(`expected_revision` 提供时),`rowcount==0` 即冲突,回读当前 revision 放进错误。
- 审计选 DB 表(`memory_edit_log`)而非新增 EventStore 事件类型:记忆工具在 executor 层执行,那里没有 EventStore 句柄;而 TOOL_CALL/TOOL_RESULT 事件已天然在会话内留痕,事件层重复记录价值有限;DB 表可跨会话查询、可直接支撑未来 studio 审计视图。字段:`id, workspace_id, agent_id, session_id?, tool_name, target_type(l1_block|user_memory|knowledge_memory), target_id, revision_before, revision_after, before_summary, after_summary(各截断 ≤200 chars), trace_id?, created_at`。备选(EventStore 新 EventType)被否如上;备选(before/after 全文入库)被否——膨胀与 PII 暴露面,摘要级即可满足追责。

### D5. 召回存储:双写(元数据表 + Qdrant),watermark 轮询 + 回合提交触发

- `recall_messages` 表:`id, workspace_id, agent_id, conversation_id, session_id, user_id?, role, content, content_hash, event_version(水位), created_at` + 唯一约束 `(session_id, content_hash, seq)` 保证幂等;Qdrant 集合 `hecate_recall` 存向量,payload 携带作用域字段(对齐 `memory-isolation` 的 payload filter 模式)。
- 索引器 `RecallIndexerService`(hecate-memory 包内):**单一数据源 = 持久事件表的 CHANNEL_WRITE(`channel="messages"`)行**,`event.version` 作为稳定 seq。触发 = 组合根托管的后台轮询(默认 60s,可配 0 关闭),按 per-session 水位(`max(event_version)`)增量索引。**As-built 偏差**:原设计的"TURN_END fire-and-forget 回调"通道未实现——pregel 主循环与 Path-A 门面是引擎热路径,侵入式回调的风险大于新鲜度收益;轮询 delivers 有界延迟(≤60s)的"异步写入",spec 场景不要求即时性。补索引机制 = 同一轮询的幂等重放(崩溃/失败批次下一轮自动追平)。
- EventStore 无订阅机制,不做侵入式改造;索引器只消费已有数据。
- embedding 复用 `rag/embedding.py`(`encode_query`/批量 encode),collection 懒创建复刻 `KnowledgeMemoryService._ensure_collection` 模式。

### D6. conversation_search 执行:provider.search_recall,游标 = 排序键偏移

游标为不透明字符串(base64 of `(score, recall_id)` last-position),服务端按 `score DESC, id` 排序续读,天然无重复。`exclude_session_ids` 在 payload filter 层做 `must_not` 过滤。时间窗过滤落在元数据字段(Qdrant range filter + SQL 双侧一致)。低信号判定:结果数 == 0,或 top1 score < `recall_low_signal_threshold`(配置,缺省 0.35)。

### D7. prefetch 与升级提示:两个"always" processor,链尾注入

- `MemoryPrefetchProcessor(run_mode="always")` 追加在默认链末尾(termination 之后不追加——`stop_reason=token_capped` 时跳过):以投影后最近 2 轮 user/assistant 文本(截断 ≤2000 chars)为查询调 `prefetch`,渲染为尾部单条 system 附加块 `<memory>...</memory>`;自带 `max_entries`/`max_tokens`(node_config 可覆写,platform settings 兜底);放在链尾天然位于 KV 保护前缀区之后,token 计入链的预算统计。备选(PreLLM hook / LLM_RESPONSE middleware 前置 stage)被否:两者拿不到投影后的单元列表与预算上下文,会产生第二套预算口径。
- `RetrievalEscalationHintProcessor(run_mode="always")`:读 `execution_context["memory_retrieval_low_signal"]`(工具执行后由 runtime 工具循环写入的当轮标记),命中则注入一次提示块并清除标记;防抖用 per-session 冷却(executor 持有的进程内 map,冷却窗口默认 120s,复用 4.13 失败策略的语义但独立实例,不与其熔断共享状态)。

### D8. Flags 与配置

`MEMORY_TOOLS_ENABLED` / `RECALL_INDEXING_ENABLED` / `MEMORY_PREFETCH_ENABLED`(均 `False`)。阈值/限额(`recall_low_signal_threshold`、prefetch 条目/ token 上限、轮询间隔)收在 settings 的 memory 段;node 级覆写走 `node_config` 透传(链已携带),不扩 context_policy 语法(留待有真实 per-node 差异诉求时再进正式 policy)。

### D9. 工具签名总表与命名

`memory_replace / memory_insert / memory_rethink / memory_search / memory_add / memory_update / memory_forget / conversation_search`(8 个)。`knowledge_insert`/`knowledge_search` 死代码定义删除,其能力由 `memory_add`/`memory_search` 覆盖(MCP server 面的 `knowledge_search` 等管理工具**不在**本变更范围,保持不动)。risk_level 全部 LOW(读)与 MEDIUM(写:L1/ L3/L4 变更),沿用 risk-level 静态表。

### D10. 检索打分合并与召回 TTL 缺省

- **`memory_search` 的 L3/L4 合并排序**:L3 沿用其既有向量相似度路径、L4 沿用既有 `HybridSearcher`(dense 0.7 / sparse 0.3,RRF k=60),两侧分数各自归一到 [0,1] 后合并排序;不引入新的融合层、不新增常数面。理由:L3/L4 检索器与常数已在生产使用,统一到同一质量基线的成本为零;新融合层属于无数据支撑的过早调参。
- **召回 TTL 缺省 = 长期保留**(`RECALL_TTL_DAYS=0` 表示永久,per-workspace 可配)。理由:召回层的定位就是超越 event retention 的持久归档(spec 明确承诺 retention 清理不伤召回),默认 TTL 会静默削弱核心价值;删除路径已有 conversation 级联清理兜底;有合规保留期限诉求的租户显式开启即可,与 retention 服务的配置风格一致。

## Risks / Trade-offs

- [召回向量写入量随对话量线性增长] → TTL 配置兜底 + Qdrant 集合按 workspace 分区命名留后路;本期不做分片,规模触发后再议。
- [轮询 + 回调双通道重复索引] → 幂等唯一约束 `(session_id, content_hash, seq)` 保证收敛,重复写入被吸收。
- [executor 层拿不到完整身份上下文(旧调用方)] → 身份缺失时 fail-closed 结构化报错,并暴露明确的修复面(调用方传全 context);不做隐式兜底解析。
- [prefetch 注入与 prompt-injection:召回内容进入 prompt] → 召回结果走既有工具结果安全通路与 guardrail hooks;`<memory>` 块为纯文本渲染,不做指令性包装;后续可接 9.x 输出/输入安全的既有链。
- [revision 并发仅覆盖工具路径] → REST 路径本期不强制 expected_revision(向后兼容),但 REST 写同样递增 revision,审计同样落 `memory_edit_log`。
- [单变更体量大(L 级)] → tasks 按依赖分 6 阶段,每阶段独立可验证、可单独合入搁置;flag 三开三关保证任意中间态可部署。

## Migration Plan

1. 迁移全部 additive:`revision` 列(server_default "1")+ `recall_messages` + `memory_edit_log` 表;无数据回填、无列改型。
2. 部署顺序:先库后码(迁移先行无害);flags 默认关,合入即部署、开启即灰度。
3. 回滚:任一 flag 关闭即回到等价旧行为;召回索引数据残留无害(查询面同时关闭)。

## Open Questions

(无——原有两个实现期参数已闭环为 D10。)
