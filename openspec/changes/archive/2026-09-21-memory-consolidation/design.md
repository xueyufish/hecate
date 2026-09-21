# Design: memory-consolidation

## Context

决策依据来自对既有代码的勘察(动机见 proposal Why)。关键既有资产与约束:

- **写路径**:L1/L3/L4 服务层(`packages/hecate-memory/src/hecate_memory/memory/{working_memory,user_memory,knowledge_memory}.py`)带 `revision` 乐观并发;所有 agent 侧变更已流经 `memory_edit_log`(不可篡改审计)。
- **审查窗口数据源**:`recall_messages`(transcript 级索引,workspace/agent/user 列齐全,`event_version` per-session 水位,设计上独立于事件保留期)。
- **触发器接缝**:4.13 `ContextProcessorChain` 开放处理器集;`BudgetWarnProcessor`(0.8, latched)是 nudge 的直接先例;`MemoryPrefetchProcessor` 确立"注入位置在 KV-cache 保护前缀之后 + 计入记账 + 失败降级"的纪律。
- **调度**:APScheduler 3.x manager(`src/hecate/ops/scheduling/manager.py`)已实现 cron + `pg_try_advisory_lock` 多实例互斥。
- **已知缺陷**:L3 `UserMemoryService` 使用 mock embedding;L3 检索实为 importance 排序 SQL。L4/recall 有真实 Qdrant 路径与 embedding client。
- **范围粒度先例**:L3 `scope` JSONB 已携带 `user_id`;整合单元与其对齐。

## Goals / Non-Goals

Goals:引擎、触发总线、nudge 处理器、审计与取代语义、配置面全部落地,默认关闭可安全发布。

Non-Goals(承 proposal):L3 迁 Qdrant;完整脱敏管线;persona 默认重写;静默 flush 回合(v2);人工批准门(v2);`event_count` 触发;效果对比验证(上线后评估任务)。

## Decisions

### D1. 引擎形态:plan-then-apply,不走 agent tool-loop

每整合单元一次有界 LLM 编排:抽取与计划生成由 LLM 产出**结构化 JSON 计划**(操作词汇 `ADD`/`UPDATE`/`SUPERSEDE`/`NO-OP`/`UPDATE_BLOCK`,字段含目标相似记忆 id、内容、置信度、类型),由确定性代码校验(允许清单、范围、预算、revision)后在单事务应用。该形态满足"原子 + 可审计 + 预算可控"的 ADR-024 约束:LLM 输出在应用前可校验、可拒绝、可完整审计。备选——让整合以受限 agent tool-loop 形态运行——被否:逐条变更非原子、运行边界难限界,而其优势(并发编辑安全)已被 revision 守卫覆盖。

### D2. 写路径:复用服务层,新增 SUPERSEDE 服务函数

整合应用不发明新写入机制:ADD/UPDATE 走各层服务的既有 create/update;SUPERSEDE 新增服务层函数(置 `superseded_by` + 软删除 + revision bump),内部同样写 `memory_edit_log`(`tool_name` 记为 `consolidation`),使在线与后台写入共享同一审计与并发语义(单一写路径原则,避免旁路写入绕过 revision 与审计)。`UPDATE_BLOCK` 经 working_memory 服务校验允许清单后执行。**不修改** `agent-memory-tools` 的 agent 工具面与 `memory-provider-contract`。

### D3. 触发总线:一个"待处理查询",三种调度策略

核心查询:存在 `recall_messages` 行其创建时间晚于该单元整合水位的 `(workspace, agent, user_id|null)` 集合。cron 与 idle 只是两种取这批单元的时机;压力标记是该查询的排序键。idle 用轮询近似(默认每 5 分钟扫一次"安静 ≥ 30 分钟且有新转录"的单元),不引入进程内 per-scope 定时器——防抖与取消合并的语义以轮询等价实现,换取无状态与可恢复(实例重启后轮询自然续上,无需重建定时器)。调度宿主复用 `ops/scheduling` 的 APScheduler + advisory lock;不新建调度器。

### D4. 水位与审计:`consolidation_runs` 兼作水位源

新表 `consolidation_runs`(id、workspace_id、agent_id、user_id、trigger、window_start/window_end、候选/采纳/拒绝/失败计数、status、error、created_at 等)。单元水位取"该单元最近一次 status=SUCCESS 的 window_end"——不另建水位表,断点续跑与审计同源(promote-before-checkpoint:仅在单元内全部操作应用成功的事务提交后,run 才记 SUCCESS)。压力标记落 `session_state` 侧表列(设计见 D6),消费后清除。

### D5. 相似度:应用内 cosine + 真实 embedding 回写

整合管线内 `_embed(text)` 复用 recall indexer 的 embedding client;候选与同单元现有 L3/L4 行(数量为几十~几百,应用内 cosine 足够)比较,超阈值的近重复生成 UPDATE/SUPERSEDE 而非 ADD。被触碰行将真实向量写回 embedding 字段(替换 mock 值),使 L3 的 embedding 列逐步真实化;不迁移 Qdrant(留给 4.14/4.15 检索质量工作)。embedding 不可用时降级:跳过相似度去重,仅按文本精确重复去重,并在 run 记录降级原因。

### D6. Nudge 处理器:`MemoryPressureNudgeProcessor`

`runtime/context_processors.py` 新处理器,`run_mode="always"`,与 `BudgetWarnProcessor` 同构:阈值默认 0.9(高于 budget_warn 0.8,留出压缩前抢救窗口),latched 跨越语义一致;注入内容为用量数字 + "将持久事实写入记忆"显式指令——具体数字比纯提示更可执行。压缩前由系统代跑的静默 flush 回合形态留 v2。注入位置与 `MemoryPrefetchProcessor` 同区(保护前缀之后),计入记账。首次跨越时向单元写压力标记(尽力而为,失败仅告警)。链注册经既有 context_policy 配置解析,平台默认不启用。

### D7. 安全最小层:候选级扫描

候选事实应用前经现有 guardrail/injection 检测接缝(以库调用形态接入;若接缝不可库调用,则 v1 退化为 prompt 级硬约束 + 凭证模式拒绝,记为 tasks 中的确认项)。凭证类内容(密钥/令牌/口令模式)无条件拒绝。候选被拒绝不中断其余候选。

### D8. 隔离与 L1 允许清单

整合单元三元组 `(workspace, agent, user_id|null)`;查询与写入全程携带三元组过滤。`UPDATE_BLOCK` 允许清单为 per-agent 配置(平台默认仅 `learned_context`);`learned_context` block 不存在时由整合按需创建(source 标记为整合,编辑日志可区分在线与后台写入)。

### D9. 配置面与默认姿态

`core/config.py` 新增(全部默认关):`CONSOLIDATION_ENABLED`、`CONSOLIDATION_SCHEDULE`(cron,默认 `0 2 * * *`)、`CONSOLIDATION_IDLE_CHECK_INTERVAL_SECONDS`(300)、`CONSOLIDATION_IDLE_QUIET_SECONDS`(1800)、`CONSOLIDATION_MAX_LLM_CALLS_PER_RUN`、`CONSOLIDATION_MAX_MUTATIONS_PER_RUN`、`CONSOLIDATION_SIMILARITY_THRESHOLD`、`MEMORY_PRESSURE_NUDGE_ENABLED`、`MEMORY_PRESSURE_NUDGE_THRESHOLD`(0.9)。per-agent 允许清单与 opt-out 走 agent 配置,经既有配置解析优先级(node > agent > platform)落位。

## Risks / Trade-offs

- **[抽取质量差污染记忆]** → 候选打分阈值 + 安全扫描 + SUPERSEDE 可回溯(软删除留档)+ `consolidation_runs` 审计;上线初期保持默认关,灰度开启后人工复查运行记录。
- **[idle 轮询的及时性弱于事件驱动]** → 轮询间隔可配;压力标记提供即时路径;v2 可演进为事件驱动而不改 spec。
- **[应用内 cosine 的规模上限]** → 单元级记忆量小(几十~几百);规模化触发时由 4.14/4.15 的向量化工作承接,Qdrant 迁移缝已留(embedding 列真实化)。
- **[后台写与在线写竞争 revision]** → 单事务应用 + revision 冲突跳过 + 下一轮重试(at-least-once);冲突率预期低(夜间/静默期)。
- **[LLM 成本失控]** → 每 run/单元调用与变更双上限 + 默认关 + 打分门槛过滤低价值候选。
- **[guardrail 接缝形态未知]** → tasks 中含确认项;退化路径(prompt 级约束)已定义,不影响 spec 行为。

## Migration Plan

1. Alembic migration:`consolidation_runs` 表;`memories`/`knowledge_memories` 增 `superseded_by`(nullable,无默认值变更,零回填需求);`memory_blocks` 无列变更(允许清单走配置)。
2. 部署顺序:先迁移后发码;所有新路径默认关,发布即安全。
3. 回滚:关闭两个开关即回到现状;`consolidation_runs` 与 `superseded_by` 为纯增量,不需回滚迁移。软删除语义与 BaseModel 一致,既有查询不受影响。

## Open Questions

(无阻塞性未知。tasks 中留两个实现期确认项:guardrail 检测是否可库调用(D7 退化路径);压力标记的具体存储位置(`session_state` 侧列 vs 轻量标记表)——两者均不改 spec 行为与任务拆分。)
