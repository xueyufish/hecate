# task-memory Specification

## Purpose
为 agent 提供"任务轨迹 + 反思 + 工作记忆图"的持久化与检索能力,把 agent 从执行历史中学到的经验沉淀为可检索、可回灌、可淘汰的记忆,使 Task Memory 在跨 session、跨 actor 的同类任务上自我改善(Work Context Graph / KM6 自改进循环)。

## Requirements

### Requirement: Episode 写入与生命周期

系统 SHALL 在每次任务执行过程中以 event 流形式记录该任务的 `Episode`,每个 episode 携带 `episode_id / workspace_id / agent_id / actor_id / session_id / task_type / situation / intent / actions / outcomes / closed_at` 字段。Episode SHALL 在任务完结(成功或失败)时打 `closed_at` 戳,触发反思入口。

- Episode 与 4.13 processor chain 的 `sync_turn` 钩子解耦:Episode 写入在事件发生时异步追加,不阻塞对话轮次。
- Tool call SHALL 作为 `TOOL` event 写入同一 episode 的 `actions` 数组;tool result 写入 `outcomes` 数组的对应项(4.22 Tool Memory 即此机制,不另建独立 store)。
- Episode SHALL NOT 因反思失败被回滚或修改;反思产物走独立 `reflections` 表,与 episode 是 1:N 引用关系。
- Episode 的 `closed_at` SHALL 由调用方显式标记;未标记的 episode 不进入反思候选池。

#### Scenario: 任务完结打 closed_at 戳

- **WHEN** 一个用户目标级任务(`task_type=expense_report_filing`)结束,调用方调用 `episode_close(episode_id)`
- **THEN** 该 episode 的 `closed_at` 被设为当前 UTC 时间,触发反思入口(若 `REFLECTION_ENABLED=true`)
- **AND** 该 episode 进入反思候选池

#### Scenario: 工具调用作为 TOOL event 写入

- **WHEN** 任务执行过程中 agent 调用工具 `crawl_url(wait_for='selector:form')` 并返回结果
- **THEN** episode 的 `actions` 数组追加一条 `{event: TOOL, tool_name, args, result_ref, ts}` 记录
- **AND** 该记录随 episode 一同进入反思候选池,反思时可被一起分析

#### Scenario: 反思失败不污染 episode

- **WHEN** 反思引擎对 episode 提取 reflection 失败(超时、模型错误、闸口拒绝)
- **THEN** episode 记录保持不变,失败原因写入 `reflection_runs.rejection_reason`,不影响 episode 数据

### Requirement: Reflection 提取与持久化

系统 SHALL 提供 `ReflectionEngine`,在 episode 完结后异步提取 reflection,产物写入 `reflections` 表。Reflection SHALL 是 typed record 而非 free-form 文本,字段包含 `reflection_id / workspace_id / actor_id / session_id / title / use_cases / hints / confidence / source_episode_ids / operator / version / superseded_by / status / isrel / issup / isuse / created_at`。

- Reflection 提取 SHALL 复用 4.5 ConsolidationEngine 的 `ExtractFn / PlanFn / ScanFn / EmbedFn` seam;Reflection 与 consolidation 是姊妹机制,共享管线骨架与 `OperationOutcome` 审计。
- Reflection `hints` 字段 SHALL 硬切分 ≤ 300 词;超出 SHALL 拒绝并记录原因。
- Reflection `source_episode_ids` 数组 SHALL ≥ 2 才允许 status=`pending`(防单条幻觉沉淀);违反 SHALL 立即拒绝(不进 LLM-as-Judge)。
- Reflection 默认 `operator='add'`;同一 `title` 已存在 active 条目时 SHALL 切换为 `operator='update'`,递增 `version` 字段。
- Reflection `status` 取值 SHALL 是 `pending / approved / rejected / deprecated` 之一;`pending` 是写入初始态,通过闸口后转 `approved`。

#### Scenario: 反射产物持久化

- **WHEN** ReflectionEngine 对一个 `closed_at` 已设的 episode 提取 reflection,产出 typed record`
- **WHEN** 该 reflection `source_episode_ids` 长度 = 3(≥ 2)
- **THEN** reflection 被写入 `reflections` 表,`status='pending'`,`version=1`,`operator='add'`
- **AND** `reflection_runs` 记录该次反射运行的统计与 confidence 分布

#### Scenario: 同 title 增量合并

- **WHEN** 一个新 reflection 提取产生 title='优先用 selector 等待' 的产物
- **WHEN** 既有 active reflection 存在相同 title
- **THEN** 新产物 `operator='update'`,`version` 在既有基础上 +1,既有条目的 `superseded_by` 指向新条

#### Scenario: 单 episode 不允许产生 reflection

- **WHEN** ReflectionEngine 对一个 episode 提取 reflection
- **WHEN** 该 reflection 的 `source_episode_ids` 长度 = 1
- **THEN** reflection 立即被拒绝,`reflection_runs.rejected_count += 1`,不进入 LLM-as-Judge 闸

#### Scenario: hints 超 300 词拒绝

- **WHEN** ReflectionEngine 产出的 `hints` 字段长度 > 300 词
- **THEN** reflection 立即被拒绝,记录 rejection_reason='hints_too_long'

### Requirement: 反思质量四道闸

系统 SHALL 对每个 `pending` 状态的 reflection 依次执行四道闸,任一闸拒绝 SHALL 立即终止并标记 `status='rejected'`。

- **闸 1 — 模型隔离**:反思 fork SHALL 仅拥有只读 episode / knowledge_memory 视图,不可调用写文件 / 网络 / secrets 工具;违反 SHALL 拒绝。
- **闸 2 — LLM-as-Judge 三 token 评分**:复用 1.3.5e GroundingScorer + 1.3.6f EvolutionGate 模式,产出 `isrel / issup / isuse` 三个 0–1 分(可检索性 / 证据支持 / 实用性三维度);三者均 > 0.5 才通过。
- **闸 3 — source_episode_ids >= 2**(已在 reflection 提取阶段执行,这里作为二次校验):任一 reflection `source_episode_ids` 长度 < 2 SHALL 拒绝。
- **闸 4 — confidence 与 system_id 淘汰**:`confidence < 0.4` 的 reflection SHALL 进入 deprecated;连续 3 次 reflection 评估仍 < 0.4 SHALL 自动 `status='deprecated'` 并写 `superseded_by=null`。

通过四道闸的 reflection SHALL 转 `status='approved'`,并被 `WorkContextGraph` 索引层消费;拒绝的 reflection SHALL 转 `status='rejected'`,不进入检索路径但保留可查询(用于审计)。

#### Scenario: 闸 1 模型隔离生效

- **WHEN** 反思 fork 试图调用写文件工具
- **THEN** 反思 run 立即拒绝,`status='rejected'`,rejection_reason='forbidden_tool_in_write'

#### Scenario: 闸 2 三 token 全过

- **WHEN** LLM-as-Judge 对 reflection 评分,产出 `isrel=0.82 / issup=0.71 / isuse=0.66`
- **THEN** 闸 2 通过,reflection 流转下一闸

#### Scenario: 闸 2 任一 token 不达标

- **WHEN** LLM-as-Judge 评分 `isuse=0.42`(< 0.5)
- **THEN** 闸 2 拒绝,`status='rejected'`,rejection_reason='low_isuse_score'

#### Scenario: 闸 4 自动淘汰

- **WHEN** 一条 approved reflection 的 confidence=0.35
- **WHEN** 该 reflection 连续 3 次被评估,confidence 仍 < 0.4
- **THEN** reflection `status='deprecated'`,从 Work Context Graph 索引层移除,可查询但不可检索

### Requirement: Work Context Graph(KM6)

系统 SHALL 将 `approved` 状态的 reflection 写入 Work Context Graph(KM6 / ADR-024 增强),该 graph 包含两类存储:`work_context_nodes`(5 类节点 `method / outcome / correction / source / pattern`)+ `work_context_edges`(4 类边 `tried_before / led_to / corrected_by / validated_by`)。节点 SHALL 携带 `success_rate / usage_count / last_used_at / user_correction_count / source_reliability` 等评分字段。

- 节点统计字段 SHALL 由后台批量 job(ReflectionEngine 的一部分)从 `episodes` 与 `reflections` 聚合更新,**不在工具调用时实时更新**(避免污染)。
- Work Context Graph SHALL 仅消费 `status='approved'` 的 reflection;`pending / rejected / deprecated` 状态 SHALL NOT 进入 graph。
- graph 节点 SHALL 与 reflection 表保持 `linked_reflection_id` 关联,reflection 被 superseded 时对应节点 SHALL 同步标记 `active=false`。
- 自改进循环(`task starts → executes → completes → next task richer graph`)SHALL 在 agent 侧通过 `reflection_search` 工具实现闭环。

#### Scenario: approved reflection 入图

- **WHEN** 一条 reflection 通过四道闸转 `status='approved'`
- **THEN** ReflectionEngine 在同一事务内创建对应 `work_context_nodes` 行(按 reflection 的 `use_cases` 与 `hints` 字段决定 node_type)与 0+ 条 `work_context_edges` 行
- **AND** 节点的 `linked_reflection_id` 指向该 reflection

#### Scenario: rejected reflection 不入图

- **WHEN** 一条 reflection 在闸 2 被拒绝,`status='rejected'`
- **THEN** `work_context_nodes` 与 `work_context_edges` 无对应行,不影响 graph 完整性

#### Scenario: reflection 被 superseded 同步切图节点

- **WHEN** 一条 approved reflection 被后续 reflection `superseded_by` 引用
- **THEN** 对应 `work_context_nodes` 行 `active=false`,但保留可查询

#### Scenario: 节点统计字段后台聚合

- **WHEN** 后台批 job 跑过一组 episodes
- **THEN** 对应 `work_context_nodes` 行的 `usage_count / success_rate / last_used_at` 被更新,实时工具调用 SHALL NOT 触发此类更新

### Requirement: 反思消费三路

系统 SHALL 提供三种反思消费路径,任一路径独立启用,均受 `REFLECTION_ENABLED` flag 约束。

- **路径 a — 任务开始检索**:Agent 调用 `reflection_search(query, task_type, top_k)` 时,系统按 `use_cases` 命中 top-K approved reflections,注入 L1 `reflection_summary` block(扩展既有 `UPDATE_BLOCK` 允许清单)。
- **路径 b — 失败重试召回**:当任务失败(evaluator confidence < 阈值)重试时,系统 SHALL 自动检索 `use_cases` 匹配失败 task_type 的 approved reflections,注入 retry context(失败时主动召回调)。
- **路径 c — EvolutionGate 评分输入**:反思 SHALL 作为 1.3.6f EvolutionGate 的输入之一,持续参与 `golden_subset_regression` 与 `trigger_test` 评估。

反思消费 SHALL 满足:
- 仅消费 `status='approved'` 的 reflection;`pending / rejected / deprecated` 不被消费。
- 注入 L1 时计入 token 预算,与 4.13 预算轴协同。
- 路径 a 的检索 SHALL 走 `memory_provider.tier-4`,路径 b 由 4.13 processor chain 的 `escalate_failure` 钩子触发。

#### Scenario: 路径 a 检索注入

- **WHEN** Agent 调用 `reflection_search(query='如何抓取表单', task_type='expense_report_filing', top_k=3)`
- **WHEN** `reflections` 表有 2 条 `status='approved'` 且 `use_cases` 匹配 task_type 的反思
- **THEN** 工具返回这 2 条反思(≤ top_k),其中 `hints` 字段合计 ≤ 300 词;被注入 L1 `reflection_summary` block

#### Scenario: 路径 b 失败召回

- **WHEN** 一个 `task_type='expense_report_filing'` 的任务执行失败
- **WHEN** evaluator 输出 confidence < 阈值,触发重试
- **THEN** 系统自动检索 `use_cases` 含 'expense_report_filing' 的 approved reflections,注入重试上下文
- **AND** reflection 注入不阻塞重试发起

#### Scenario: path c 评分参与反思评估

- **WHEN** EvolutionGate 跑 `golden_subset_regression` 评估
- **THEN** 反思通过 `isrel / issup / isuse` 三 token 与历史 baseline 对比,confidence < 0.4 的反思被标记 deprecated

#### Scenario: 关闭 flag 行为不变

- **WHEN** `REFLECTION_ENABLED=false`(默认)
- **THEN** 三路消费路径全部禁用,平台行为与本次变更合入前 byte-identical

### Requirement: REFLECTION_ENABLED 默认关闭

系统 SHALL 提供 `REFLECTION_ENABLED` flag,默认 `false`。`false` 时:
- `ReflectionEngine` 不启动,`episodes` 表存在但不被消费。
- `reflection_search` / `work_context_query` 工具不出现在 seeding 集合。
- 4.13 processor chain 的 `ReflectionRecordProcessor` 不注册。

`true` 时上述能力全部启用。flag 切换 SHALL 不需要重启,可在运行期切换。

#### Scenario: 默认 flag 关闭

- **WHEN** 平台以默认配置启动
- **THEN** `REFLECTION_ENABLED=false`,Task Memory 相关工具不出现,ReflectionEngine 不启动

#### Scenario: flag 切换无需重启

- **WHEN** 管理员将 `REFLECTION_ENABLED` 从 `false` 切到 `true`
- **THEN** 系统在下一调度周期内启动 ReflectionEngine,工具出现在下一个 seeding 周期
- **AND** 切换不影响在线会话

### Requirement: Task Memory 租户隔离

Task Memory SHALL 以 `workspace_id + actor_id` 为第一层隔离:`episodes` / `reflections` / `work_context_nodes` / `work_context_edges` 表的查询 SHALL 强制 `workspace_id` 过滤;`actor_id` 在 actor-scoped review 时限制访问(可声明 open 的反思跨 actor 共享,见 Work Context Graph)。跨 workspace 读写 SHALL 返回结构化错误,行为与现有 `agent-memory-tools` 的隔离约束一致。

#### Scenario: 跨 workspace 不可达

- **WHEN** workspace X 的 agent 以 workspace Y 的 episode_id 调用 reflection 检索
- **THEN** 返回"episode 不存在"结构化错误,workspace Y 数据不变
