# Design

## Context

当前 `packages/hecate-memory` 已实现 4.5 consolidation、4.14 融合排序、4.15 重要度评分、4.17 pressure alert、4.18–4.20 agent memory tools,以及 `memory-provider-contract`(tier-1/2/3 + prefetch / sync_turn)。1.3.6f self-evolution 与 1.3.5e grounding scoring 也已经 ship。本次新增的 Task Memory / Tool Memory / Cross-Thread Memory Store 需要在不破坏既有契约的前提下,扩展 provider tier 与 lifecycle 钩子,新增 4 张表 + 1 个引擎,并在 4.13 processor chain 上挂接新的 processor。详见 proposal.md 的 why / what。

## Goals / Non-Goals

**Goals:**

- 在 `packages/hecate-memory` 内新增 `task_memory/` 子包,落 episodes / reflections / work_context_nodes / work_context_edges 四张表
- 把 4.5 ConsolidationEngine 的 `ExtractFn / PlanFn / ScanFn / EmbedFn` seam 抽出,新增 `ReflectionEngine` 作为姊妹机制(共享管线骨架,独立 trigger / 独立审计)
- 扩展 `MemoryProvider` capability 声明,新增 tier-4 / tier-5 + `end_episode` / `escalate_failure` lifecycle 钩子;向后兼容(老 provider 不声明仍工作)
- 在 4.13 processor chain 上注册 `ReflectionRecordProcessor`,在 `sync_turn` 后挂接,负责 episode 写入与反思触发
- 在 L3 / L4 表上扩展 `team_id` / `actor_id` 列(可空),加索引 + namespace 校验
- 反思消费侧三路集成:任务开始检索 / 失败重试召回 / EvolutionGate 评分输入
- 默认 flag `REFLECTION_ENABLED=false`,开启后才进入 active 状态

**Non-Goals:**

- 不做"全自动 prompt / skill 自演进"(演进权保留在人类侧,agent 不可自动变更 prompt / skill)
- 不做反思自动改 agent 代码
- 不做 graph traversal rerank(实验性,易污染)
- 不做 MEMORY.md 风格的全量注入
- MVP 阶段 graph 存储走关系表,不引入 Neo4j;后续可切
- 不实现 4.24 memory versioning(独立 change 跟进)

## Decisions

### D1. ReflectionEngine 与 ConsolidationEngine 共享 seam

**选择**:抽出 4.5 ConsolidationEngine 的 `ExtractFn / PlanFn / ScanFn / EmbedFn` seam,新增 `ReflectFn`,两者跑同一 LLM 路由 + 同一审计管线 + 同一调度总线。

**理由**:reflections 与 consolidation 是同一抽象族的两种实例化(一个管对话事实,一个管任务经验),共用骨架避免双份维护;`reflection_runs` 与 `consolidation_runs` 平行表,独立审计指标。

**替代方案考虑**:
- 全独立(完全新建一套引擎):重复 4.5 已投入的审计、调度、水位、互斥代码;不取。
- 合并为同一个引擎:语义不同(consolidation 操作 L3/L4 fact,reflection 操作 reflections 表),合并会让引擎抽象变胖;不取。

### D2. Tool Memory 不另建 store,作为 episode 的 TOOL event

**选择**:tool call 在 `sync_turn` 钩子处作为 `{event: TOOL, tool_name, args, result_ref, ts}` 写入当前 active episode 的 `actions` 数组;反思时与 episode 一起被分析。

**理由**:主流 episode 模型即采用此设计(把工具调用作为 episode 的一部分而非独立存储);Hecate 现有 `tool-decision-log` 已覆盖工具调用的可观测性,Tool Memory 的差异化在于"反思时一起分析"而不是"另开一个存储"。

**替代方案考虑**:
- 另建独立 `tool_memory` 表 + 独立 schema:增加表 / 索引 / 检索路径,但检索与反思时机与 episode 强耦合,合并存储更直接。
- 复用 `tool-decision-log` 表直接做 memory:decision log 是 append-only 审计,不可写 revision / confidence 等反思字段,合并会破坏审计不可篡改约束。

### D3. Cross-Thread Memory 不另建 store,在 L3/L4 上加 namespace 维度

**选择**:L3 / L4 表新增可空 `team_id` 与 `actor_id` 列(替代既有 `user_id` 列的语义);存储复用既有 `memories` / `knowledge_memories` 表。

**理由**:主流 namespace 模型采用维度化扩展;Hecate 已用 workspace_id 隔离,加 actor/team 是自然扩展。

**替代方案考虑**:
- 另建 `cross_thread_memories` 表:增加 schema / 索引重复,但 namespace 维度同样需要,等于做两遍;不取。
- 用独立 graph DB:成本与运维负担过重,MVP 不取。

### D4. Work Context Graph MVP 走关系表 + 边表,后续可切 graph store

**选择**:`work_context_nodes` 与 `work_context_edges` 是两张 Postgres 表,边类型 `tried_before / led_to / corrected_by / validated_by` 由 `edge_type` 列枚举;`work_context_edges.source_node_id` / `target_node_id` 引用 node 表。

**理由**:MVP 阶段 graph 规模小(每 agent < 10K 节点),关系表 join 性能足够;ADB-017 GraphStore ABC 保留未来切 Neo4j 的余地。

**替代方案考虑**:
- 直接用 Neo4j:增加运维与依赖,graph 规模未达必要;不取。
- 用 PG 递归 CTE 模拟:本质就是关系表 join,显式建表更可控。

### D5. 反思质量四道闸,默认不进图

**选择**:每条反思在 `pending` 状态下依次过四道闸(模型隔离 → LLM-as-Judge 三 token 评分 `isrel / issup / isuse` → `source_episode_ids >= 2` 二次校验 → `confidence < 0.4` 三次评估自动 deprecated),任一闸拒绝即 `status='rejected'`,不进入 Work Context Graph。

**理由**:调研结论("不要反思更多,要反思更准、更便宜、更可回滚");每道闸对应一个已知失败模式:
- 闸 1 防 stealth memory 注入(反思 fork 拿不到写工具)
- 闸 2 用三 token 评分(可检索性 / 证据支持 / 实用性)把质量评估嵌入输出
- 闸 3 防 Single-Episode Hallucination
- 闸 4 自动淘汰低 confidence 反思

**替代方案考虑**:
- 仅一道闸(LLM-as-Judge):单一闸容易被绕过;不取。
- 用 write_approval 强制 admin 审批:运营负担过重,默认 flag 关闭已挡掉大部分;只对 `confidence > 0.8` 的高影响反思启用 staging 是后续可考虑项。

### D6. 反思默认 `REFLECTION_ENABLED=false`,运行时可改

**选择**:对照 `MEMORY_TOOLS_ENABLED` 默认 `false` 的设计,反思层默认关闭;开启后 `ReflectionEngine` 启动、`reflection_search` / `work_context_query` 工具 seeding、4.13 processor 注册。

**理由**:反思层是新增能力,默认开启会让所有现有部署立刻有 LLM 调用增加 + 数据库新增表行为,违反"变更合入前 byte-identical"原则。

**替代方案考虑**:
- 默认开启:违反既有 `MEMORY_TOOLS_ENABLED` 默认 `false` 的设计一致性;不取。
- 仅 schema 默认开启 / 仅 hook 默认开启:粒度过细,运营负担反而高;不取。

### D7. 反思 LLM 路由走 flash 级别模型,与主对话模型解耦

**选择**:反思引擎的 LLM 调用走 `llm-routing` spec 中配置的反思专用模型(默认 flash 级,如 `gemini-3-flash-preview`),与主对话模型解耦;每条反思受 `REFLECTION_MAX_TOKENS` 配置约束。

**理由**:反思 LLM 调用频次高(每 episode 一次),成本敏感;调研结论(反思成本应是主对话 1/5~1/10);flash 级模型对结构化反思提取够用。

**替代方案考虑**:
- 复用主对话模型:成本不可控;不取。
- 给每 agent 单独配反思模型:运维负担过重,MVP 不取。

### D8. Episode 完结由 agent 显式 `episode_close()` 调用

**选择**:`episode_close(episode_id)` 由 agent runtime(workflow 节点完成 / 任务失败等)在合适时机显式调用,打 `closed_at` 戳并触发反思入口。

**理由**:agent 是"任务"语义的权威定义方,自动推断 task 边界不准确;显式 API 留给 agent 决定何时算任务完结。

**替代方案考虑**:
- 全自动:基于时间窗口或 LLM 决策判定 task 边界;实验性、不准确;不取。
- 与 `sync_turn` 钩子合并:对话结束不等于任务结束(可能多轮才算一个任务);不取。

## Risks / Trade-offs

- **[Risk]** 反思失控反向污染记忆 → **Mitigation**:四道闸 + 默认 flag 关闭 + write_approval staging(可选,后续可加)。
- **[Risk]** 反思 LLM 成本失控 → **Mitigation**:flash 级别路由 + 每单元上限约束 + `REFLECTION_MAX_RUNS_PER_MIN` 节流。
- **[Risk]** Work Context Graph 节点统计字段频繁更新引起 contention → **Mitigation**:后台批 job 聚合,**不在工具调用时实时更新**(写路径与读路径分离)。
- **[Risk]** 四层 namespace 加索引后查询性能下降 → **Mitigation**:在 `(workspace_id, team_id, actor_id)` 上建复合索引;workspace_id 单索引保持;定期 vacuum。
- **[Risk]** `reflections` 表膨胀过快 → **Mitigation**:`confidence < 0.4` 三次评估自动 deprecated + 反思 retention_archiving 与 consolidation 复用(后续 change)。
- **[Risk]** graph 表在大量 reflection 时 join 慢 → **Mitigation**:MVP 阶段 graph 规模有限,后续切 Neo4j(留 ADR-017 GraphStore ABC 出口)。
- **[Risk]** 反思提取 LLM 调用与主对话 LLM 调用争抢配额 → **Mitigation**:`llm-routing` 路由独立模型,反思配额与主对话配额分账。

## Migration Plan

1. **迁移阶段 1(默认 flag 关闭,无运行时影响)**:新增 4 张表 + 列扩展 + provider capability 扩展;升级后默认 `REFLECTION_ENABLED=false`,既有部署行为 byte-identical。
2. **迁移阶段 2(管理员主动开启)**:workspace admin 显式 `REFLECTION_ENABLED=true` 启用反思;`memory_search` 新增 tier 参数,既有调用不变。
3. **回滚**:每个迁移都包含 `down()` 方法;反思相关表 / 列 / capability 声明可单独撤销,不影响既有 L3/L4。
4. **既有数据回填**:L3/L4 既有 `user_id` 值回填到 `actor_id`,`team_id=null`;回填 `memory_edit_log` 标记(`backfill_reason='4.23_namespace_expansion'`)。
5. **OpenSpec 流程**:`archive` 阶段把 delta spec 合入 main spec(`openspec/specs/task-memory/spec.md`、`openspec/specs/cross-thread-memory-store/spec.md`、`openspec/specs/memory-provider-contract/spec.md`、`openspec/specs/memory-consolidation/spec.md`、`openspec/specs/agent-memory-tools/spec.md`)。

## Open Questions

- **OQ1** —— Work Context Graph 的 `success_rate` EMA(指数移动平均)窗口长度:α 参数建议 `0.1`(每 10 次评估权重衰减一半),待 PRD 阶段确认。决策窗口:进入 `apply` 阶段前可敲定。
- **OQ2** —— `REFLECTION_MAX_RUNS_PER_MIN` 默认值:MVP 建议 `5`,确保不会让 LLM 配额瞬时打满;待运营调优。决策窗口:第一轮 P1 集成测试后。
- **OQ3** —— `reflection_summary` block 的 token 上限:建议 `1500`(每反思 300 词 × 5 反思);待第一轮反思数据形态稳定后调优。决策窗口:`apply` 阶段 schema 实现时。