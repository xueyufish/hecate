# Proposal: memory-consolidation (4.5 Sleep-time Memory Consolidation + 4.17 Memory Pressure Alert)

## Why

Sprint 8 Memory Intelligence 的两项承诺(ADR-024 §3 KM3;feature-catalog 4.5/4.17)尚无实现:

1. **记忆整合缺乏后台形态**。现有记忆能力(4.19 工具层、recall 索引)全部是在线、回合内的:持久事实的沉淀完全依赖 agent 在对话中主动调用工具。Hecate 是常驻多租户平台,天然存在空闲窗口——应由后台整合在空闲期统一完成事实抽取、去重与陈旧清理,而当前没有任何机制承担这一职责。
2. **压缩前的记忆保护缺失**。4.13 处理器链在预算压力下会注入节省提示、执行压缩甚至截断上下文,但没有任何机制提醒 agent 在上下文丢失前把持久事实写入记忆;agent 已有可写记忆工具(4.19),却得不到"该存了"的信号。

同时,L3 记忆检索层当前使用 mock embedding(`UserMemoryService._generate_mock_embedding`),整合所需的相似度计算(去重/新颖性)需要一条真实的 embedding 路径。

## What Changes

- **整合引擎**(4.5 核心):三段管线——事实抽取 → importance/novelty 打分与相似去重 → 生成类型化操作计划(ADD/UPDATE/SUPERSEDE/NO-OP)并由代码校验后原子应用;LLM 仅产出计划,应用走与在线 memory tools 相同的服务层写路径,revision 乐观并发与 `memory_edit_log` 审计自然复用。
- **触发总线**:三种触发策略共享同一"待处理 scope"查询——cron 定时(`fixed_interval`,默认 02:00)、idle 静默轮询(scope 安静超过阈值且有新转录)、压力标记优先(被 4.17 标记的 scope 下轮优先处理);多实例经 PostgreSQL advisory lock 去重,水位表支持断点续跑。
- **Supersession 语义**:被取代记忆置 `superseded_by` 指针 + 软删除,永不物理删除;新增运行级 `consolidation_runs` 审计表(窗口、统计、计划、逐条 applied/rejected)。
- **安全最小层**:候选事实应用前过 guardrail/injection 检测;凭证类内容永不入库。
- **整合单元**:`(workspace, agent, user_id | null)` 三元组,与 L3 `scope` JSONB 对齐;`learned_context` block 保持 agent 级,杜绝跨用户泄漏。
- **L1 重写按全量设计、按最小交付**:操作词汇保留 `UPDATE_BLOCK` + per-agent 允许清单,默认清单仅含专用 `learned_context` block;放开 persona 等任意 block 是后续配置翻转而非代码变更。
- **4.17 Memory Pressure Alert**:新增 `MemoryPressureNudgeProcessor`(`budget_warn` 的兄弟处理器,latched),跨阈值时注入带用量数字的显式持久化指令,并给会话打压力标记供触发总线消费。
- **配置面**:全部能力默认关(照 `RECALL_INDEXING_ENABLED` 先例);per-agent opt-out 经允许清单表达;LLM 调用数与变更数按 run/scope 设上限。
- **相似度**:整合管线内置 `_embed()`(复用 recall indexer 的 embedding client),候选 vs 同 scope 现有记忆做应用内 cosine;被触碰行回写真 embedding(替换 mock 列值)。L3 迁 Qdrant 不在本 change。

**Non-goals**(明确不做,防范围蠕变):L3 embedding 迁 Qdrant;完整脱敏管线(仅做候选扫描最小层);persona 等 block 的默认重写(默认仅 `learned_context`);压缩前静默 flush 回合(v2,本期 nudge 为模型可见提示);整合产物人工批准门(v2);`event_count` 等高频触发模式(预留命名,后补);效果对比验证(作为上线后评估任务)。

## Capabilities

### New Capabilities

- `memory-consolidation`: 定时/静默/压力驱动的后台记忆整合——触发总线、整合引擎(抽取/打分/计划/应用)、SUPERSEDE 取代语义、`consolidation_runs` 审计、候选安全扫描、多实例互斥与水位、整合单元隔离、配置面。
- `memory-pressure-alert`: token 压力下的记忆保护——`MemoryPressureNudgeProcessor` 注入模型可见的持久化指令、会话压力标记、与整合触发总线的联动、降级行为。

### Modified Capabilities

(无。整合写入经服务层与既有 memory tools 共享写路径,不改变 `agent-memory-tools`、`memory-provider-contract` 的 agent 可见行为;`context-processor-chain` 的处理器集是开放配置,新增处理器不构成其 spec 变更。)

## Impact

- **代码**:`packages/hecate-memory`(新增 `consolidation` 模块:引擎、触发查询、嵌入)、`src/hecate/core/composition/wiring.py`(调度器/处理器接线)、`src/hecate/runtime/context_processors.py`(`MemoryPressureNudgeProcessor`)、`src/hecate/core/config.py`(配置项)、`src/hecate/models/memory.py` + Alembic migration(`consolidation_runs` 表、L3/L4 `superseded_by` 列)。
- **复用**:`ops/scheduling`(APScheduler + advisory lock)、recall indexer 的 embedding client、`memory_edit_log`、4.13 链注册机制、guardrail/injection 检测接缝。
- **数据**:新增表 `consolidation_runs`;`memories`/`knowledge_memories` 增列;不破坏现有查询(软删除语义与 BaseModel 一致)。
- **文档**:feature-catalog 4.5/4.17 条目、roadmap Sprint 8 状态、`docs/design/positioning.md`(archive 阶段)。
