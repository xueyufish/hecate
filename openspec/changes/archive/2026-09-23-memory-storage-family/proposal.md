# Proposal: memory-storage-family

## Why

Hecate 当前的记忆体系只能记住"用户说过什么"(L3)和"文档里有什么"(L4),但 **agent 不会从自己做过的事情里学到东西**。具体三个痛点:

1. 同一种工作做了 10 次,第 11 次还是从头摸索 —— 没有任务记忆
2. 工具调成功了不知道、失败了也不知道,下次还是乱试参数 —— 没有工具记忆
3. 一个用户调教出来的经验,另一个用户/团队用不到 —— 没有跨用户/跨线程的共享

这三个痛点对应 `roadmap.md` 中的 4.21 / 4.22 / 4.23 三个 feature。本次变更把三块合包到一个 change 一起 ship,补齐"任务记忆 + 工具经验 + 跨线程共享"三层,让 Hecate 在企业级 agent 平台的记忆能力上具备**自我进化闭环**(self-improving loop)。

## What Changes

- **新增 Task Memory 层(4.21)**:为每一次有 outcome 的用户目标级任务存一条 `Episode`(`situation / intent / actions / outcomes`),后台异步跑反思(reference)提取 `Reflection`(typed record + confidence + version + 反思污染防御闸口)。反思产物支撑 Work Context Graph(KM6 / ADR-024 增强)——5 类节点(`method / outcome / correction / source / pattern`)+ 4 类边的结构化工作记忆图。**Tool Memory(4.22)作为同一 store 下的策略变体**:tool call 作为 `TOOL` event 写入 episode,反思时一起分析"工具+参数→结果",不另建独立 store。
- **新增 Cross-Thread Memory Store(4.23)**:不另建独立 store,而是给现有 L3/L4 加 `actorId` namespace 维度(workspace / team / actor / session 四层)。在多租户多用户场景下支持"工作空间级共享"与"用户/团队级隔离"。namespace 模型采用 workspace / team / actor / session 四层结构(业界验证的多租户隔离模式)。
- **新增 `ReflectionEngine`**:复用 4.5 `ConsolidationEngine` 的 `ExtractFn / PlanFn / ScanFn / EmbedFn` seam 与 `OperationOutcome` 审计体系;`consolidation_runs` 表新增 `trigger='reflection'` 枚举值复用,新增 `reflection_runs` 平行表(独立审计反思层健康指标)。
- **新增反思质量门控**:每条反思必须过 4 道闸(模型隔离 → LLM-as-Judge 三 token 评分 `IsRel / IsSup / IsUse` → `source_episode_ids >= 2` 防单条幻觉 → `confidence < 0.4` 三次评估自动淘汰)。复用 1.3.6f `EvolutionGate` 四检 + 1.3.5e `GroundingScorer`。
- **新增消费侧三路**:`(a)` 任务开始 `reflection_search(query, task_type)` top-K 注入 L1 `reflection_summary` block;`(b)` 任务失败 retry 主动召回匹配 `use_cases` 的反思;`(c)` 反思参与 1.3.6f EvolutionGate 评分。
- **新增生命周期钩子**:在 `sync_turn` 之外新增 `end_episode` 钩子(任务完结时触发反思)与 `escalate_failure` 钩子(任务 confidence 低于阈值时也触发反思,基于 confidence 阈值触发而非每轮触发)。

### Breaking / Incompatible

- **不引入任何破坏性变更**。默认 flag `REFLECTION_ENABLED=false`(对照 `MEMORY_TOOLS_ENABLED` 默认 `false` 的设计)。关闭时系统行为与本次变更合入前 byte-identical。
- **不破坏现有 `memory-provider-contract` 的 tier-1/2/3 语义**;新增的 tier-4 / tier-5 是可选声明,声明缺失则旧 provider 行为不变。

## Capabilities

### New Capabilities

- `task-memory`:Task Memory 层。覆盖 4.21(任务轨迹存为 Episode)+ 4.22(工具调用作为 `TOOL` event 写入 episode)+ KM6 / ADR-024(Work Context Graph,反思产物结构化入图)。含 `EpisodeEvent` / `Reflection` / `WorkContextNode` 三张核心表、`ReflectionEngine` 流水线、消费侧三路集成,以及反思质量四道闸。
- `cross-thread-memory-store`:Cross-Thread Memory Store 层。覆盖 4.23。扩展既有 L3 / L4 的 namespace 模型为 `workspace_id / team_id / actor_id / session_id` 四层;复用既有 `MemoryProvider` tier-2 / tier-3 写路径,新增 namespace 维度校验与 IAM-风格的多租户隔离;新增 `CrossThreadMemoryStrategy` 跨 session 共享策略。

### Modified Capabilities

- `memory-provider-contract`:扩展 `MemoryProvider` 能力声明 — 新增 tier-4(任务/工具记忆:episode 写入 + reflection 检索 + work context graph 查询)与 tier-5(跨线程记忆:跨 session/actor 的 fact 共享与检索)。扩展 `sync_turn` 钩子语义,新增 `end_episode` 与 `escalate_failure` 钩子(可选,provider 可声明支持)。新增 namespace 维度变量(actor / team)。
- `memory-consolidation`:扩展 `consolidation_runs.trigger` 枚举,新增 `reflection` 类型;扩展整合单元 `(workspace, agent, user_id)` 为 `(workspace, team_id | null, agent_id, actor_id | null)`,与 4.23 namespace 对齐;新增 `reflection_runs` 平行审计表(独立指标:`reflection_trigger_rate / reflection_rejection_rate / reflection_pollution_rate`)。
- `agent-memory-tools`:**BREAKING-EXTENSION** — `memory_search` 工具语义扩展,新增可选参数 `tier`(`tier_1 | tier_2 | tier_3 | tier_4 | tier_5`),默认 `tier_2`(行为与现状 byte-identical);Agent 可显式查 task memory / cross-thread memory。**新增工具** `reflection_search(query, task_type, top_k)` 与 `work_context_query(query, node_type, top_k)`,受 `REFLECTION_ENABLED` flag 约束。

## Impact

### 受影响代码 / 模块
- `packages/hecate-memory/src/hecate_memory/memory/`:新增 `task_memory/` 子包(episode、reflection、work_context_graph 三个模块),新增 `cross_thread/` 子包
- `packages/hecate-memory/src/hecate_memory/memory/consolidation.py`:扩展 trigger 枚举,新增 reflection engine seam
- `packages/hecate-memory/src/hecate_memory/memory/provider.py`:扩展 tier-4 / tier-5 capability 声明
- `src/hecate/runtime/tools/memory_tools.py`:扩展 `memory_search` tier 参数;新增 `reflection_search` 与 `work_context_query`
- `src/hecate/runtime/processor_chain.py`:新增 `ReflectionRecordProcessor` 在 `sync_turn` 后挂接
- `src/hecate/runtime/evolution.py`:扩展 `EvolutionGate` 增加 `grounding_regression` 检
- `alembic/versions/`:新增 4 张迁移(`episodes`、`reflections`、`work_context_nodes`、`work_context_edges`),扩展 `consolidation_runs` 列

### 受影响 API / 协议
- `MemoryProvider` protocol 扩展两个可选能力(tier-4 / tier-5),向后兼容
- 8 个现有 memory tool 行为不变;新增 2 个 tool(reflection_search / work_context_query)

### 受影响 spec / 文档
- `docs/design/memory-design.md`:新增 Task Memory / Tool Memory / Cross-Thread 章节
- `docs/design/adr/024-knowledge-memory-enhancement.md`:KM6 实现状态从"Proposed"转"Accepted"
- `docs/concepts/memory.md`:L1-L4 概念文档扩展

### 依赖 / 风险
- 依赖 4.5 consolidation 已交付(✓)
- 依赖 4.17 pressure alert 已交付(✓)
- 依赖 1.3.5e grounding scoring 已交付(✓)
- 依赖 1.3.6f self-evolution 已交付(✓)
- 依赖 4.13 processor chain 已交付(✓)
- 风险:反思层若失控会污染记忆;4 道闸 + 默认 flag 关闭 + write_approval staging 机制三重保险
- 风险:cost profile 不确定;反射模型走 flash 级别 + 路由器层(只在 confidence 阈值以下触发)
- 风险:graph store 选型(Postgres 关系表 vs 独立 graph DB);MVP 阶段走 Postgres + 表 join,后续可切 Neo4j