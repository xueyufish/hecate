# agent-memory-tools Specification

## Purpose

定义 Agent 可调用的记忆工具面:记忆编辑(L1 记忆块精确/整块操作)、事实记忆检索与修正(L3/L4,带 revision 乐观并发与审计)、弱检索升级门控,以及工具注册、开关与租户隔离的外部可观察行为。

## Requirements

### Requirement: 记忆工具注册与开关

系统 SHALL 注册 10 个记忆类内置工具:

- **既有 8 个**:`memory_replace` / `memory_insert` / `memory_rethink`(L1 记忆块编辑)、`memory_search` / `memory_add` / `memory_update` / `memory_forget`(L3/L4 事实记忆)、`conversation_search`(会话召回,行为由 `conversation-recall` capability 定义);
- **新增 2 个**(`REFLECTION_ENABLED=true` 时注册):`reflection_search`(反思检索)、`work_context_query`(Work Context Graph 查询)。

工具 SHALL 经既有内置工具 seeding 通路注册,Agent 按名称挂载。

- `MEMORY_TOOLS_ENABLED` 默认关闭;关闭时既有 8 个工具不出现在 seeding 集合,平台行为与该变更合入前 byte-identical。
- `REFLECTION_ENABLED` 默认关闭;关闭时新增的 `reflection_search` / `work_context_query` 不出现在 seeding 集合,行为不变。
- `conversation_search` 额外受 `RECALL_INDEXING_ENABLED` 约束。

在平台 flag 允许集之上,系统 SHALL 叠加生效记忆策略的工具面收敛(见 `memory-policy` capability):Agent 实际可见的记忆工具集合 SHALL 为平台 flag 允许集与生效策略工具子集的交集;策略 SHALL NOT 使任何工具越过平台 flag 出现(策略只能收窄,不能扩权)。无生效策略时,可见集合与平台 flag 语义单独决定的结果一致。

#### Scenario: `MEMORY_TOOLS_ENABLED=false` 行为不变

- **WHEN** `MEMORY_TOOLS_ENABLED=false`(默认)
- **WHEN** 平台启动并 seeding 内置工具
- **THEN** 既有 8 个记忆工具不出现在工具注册表,Agent 无法挂载,既有工具行为不变
- **AND** `REFLECTION_ENABLED` 不影响 seeding,因为新增 2 个工具与既有 8 个工具的注册互不依赖

#### Scenario: `REFLECTION_ENABLED=false` 行为不变

- **WHEN** `REFLECTION_ENABLED=false`(默认)
- **WHEN** 平台启动并 seeding 内置工具
- **THEN** `reflection_search` / `work_context_query` 不出现在工具注册表,Agent 无法挂载,既有工具行为不变

#### Scenario: flag 关闭时平台行为不变

- **WHEN** `MEMORY_TOOLS_ENABLED=false` 且 `REFLECTION_ENABLED=false`(均为默认)
- **WHEN** 平台启动并 seeding 内置工具
- **THEN** 平台行为与本次变更合入前 byte-identical,任何记忆类工具均不出现

#### Scenario: 既有 8 个工具开启后可挂载

- **WHEN** `MEMORY_TOOLS_ENABLED=true` 且 `REFLECTION_ENABLED=false`
- **WHEN** Agent 配置引用记忆工具名称
- **THEN** 既有 8 个工具出现在 Agent 的工具列表并可被模型调用,新增 2 个工具不出现

#### Scenario: 开启后可挂载

- **WHEN** `MEMORY_TOOLS_ENABLED=true` 且 `REFLECTION_ENABLED=true`(两个 flag 均开启)
- **WHEN** Agent 配置引用记忆工具名称(含 `reflection_search` / `work_context_query`)
- **THEN** 10 个工具全部出现,Agent 可按名挂载

#### Scenario: 两个 flag 都开启时全部 10 个工具可挂载

- **WHEN** `MEMORY_TOOLS_ENABLED=true` 且 `REFLECTION_ENABLED=true`
- **THEN** 10 个工具全部出现,Agent 可按名挂载

#### Scenario: 策略收窄工具面

- **WHEN** `MEMORY_TOOLS_ENABLED=true` 且某 agent 的生效策略工具子集不含 `memory_forget`
- **THEN** 该 agent 的工具列表中不出现 `memory_forget`,其余记忆工具照常出现;其他无策略 agent 不受影响

#### Scenario: 策略不能扩权

- **WHEN** `MEMORY_TOOLS_ENABLED=false` 且某 agent 的生效策略工具子集包含全部记忆工具
- **THEN** 该 agent 的工具列表中仍不出现任何记忆工具(平台 flag 是硬上界)

### Requirement: L1 记忆块编辑语义

`memory_replace` / `memory_insert` / `memory_rethink` SHALL 分别以精确替换、行插入、整块重写三种语义操作当前 Agent 的 L1 记忆块:

- `memory_replace(label, old_string, new_string)`:块内精确子串替换;`old_string` 无匹配或匹配多处 SHALL 返回结构化错误(不执行任何修改);`new_string` 为空即删除该片段;精确重复的旧内容 SHALL 拒绝重复写入。
- `memory_insert(label, insert_text, insert_line)`:在指定行后插入(缺省追加到块尾,`0` 表示块首);禁止携带行号前缀的内容。
- `memory_rethink(label, new_content)`:整块重写;`label` 不存在时返回结构化错误(不自动建块)。
- 编辑后块内容超过该块 token limit 时 SHALL 显式报错(不静默截断),错误信息包含当前用量与块上限。
- 目标 `label` 不存在时(除 rethink 同规则外)`memory_replace`/`memory_insert` 亦返回结构化错误。
- 工具结果 SHALL 返回编辑后的块内容与最新 revision。

#### Scenario: 精确替换成功
- **WHEN** Agent 调用 `memory_replace(label="persona", old_string="喜欢简洁回复", new_string="偏好结构化表格回复")`
- **THEN** 块内该子串被替换,块 revision 递增,工具结果包含更新后内容

#### Scenario: 歧义定位拒绝执行
- **WHEN** `old_string` 在块内出现多次
- **THEN** 返回结构化错误(指明匹配次数),块内容与 revision 不变

#### Scenario: 超限显式报错
- **WHEN** 编辑后的块内容超过块 token limit
- **THEN** 返回结构化错误,包含当前用量与上限,块内容保持编辑前状态

#### Scenario: 跨 Agent 记忆不可见
- **WHEN** Agent A 调用 `memory_rethink` 操作 Agent B 的记忆块 label
- **THEN** 返回"块不存在"结构化错误,Agent B 的块内容不变

### Requirement: revision 乐观并发

L1 记忆块、L3 用户记忆、L4 知识记忆的每条记录 SHALL 携带单调递增的 `revision` 字段;每次成功修改递增。`memory_update` / `memory_forget` SHALL 接受可选 `expected_revision` 参数:

- 提供 `expected_revision` 且与服务端当前值不一致时,SHALL 返回 revision 冲突结构化错误(含当前 revision),不执行修改;
- 未提供时按"最后写入获胜"执行并递增 revision。

#### Scenario: 并发修改检出冲突
- **WHEN** 两个工具调用以相同 `expected_revision=3` 并发修正同一记忆条目
- **THEN** 恰有一个成功并将 revision 推进到 4,另一个收到冲突错误(含当前 revision=4)

#### Scenario: 未带 expected_revision 正常执行
- **WHEN** `memory_update` 不提供 `expected_revision`
- **THEN** 修改成功,revision 在原值上递增

### Requirement: 记忆编辑审计

所有经工具发起的记忆变更(L1 编辑、L3/L4 update/forget)SHALL 写入一条审计记录,包含:工具名、目标类型与 ID、操作前后 revision、操作前后的内容摘要、发起 Agent、workspace、会话与 trace 标识、时间戳。审计记录 SHALL 只读可查询,Agent 无法通过任何记忆工具修改或删除审计记录。

#### Scenario: 每次编辑留痕
- **WHEN** Agent 调用 `memory_rethink` 成功重写一个 L1 记忆块
- **THEN** 产生一条审计记录,记录块 ID、revision 变化、内容摘要、Agent 与会话标识

#### Scenario: 审计不可篡改
- **WHEN** Agent 调用任何记忆工具试图修改/删除审计记录
- **THEN** 无对应操作面(工具不支持以审计记录为目标),审计记录保持完整

### Requirement: L3/L4 事实记忆工具语义

`memory_search` SHALL 在原 tier-2(L3 + L4)基础上扩展可选 `tier` 参数:

- 取值:`tier_1 | tier_2 | tier_3 | tier_4 | tier_5`,默认 `tier_2`(行为与现状 byte-identical)。
- `tier_4`:返回反思与 work context graph 节点(走 `task-memory` capability 的 reflection / WCG 检索路径)。
- `tier_5`:返回跨 session / 跨 actor / 跨 team 的共享事实记忆(走 `cross-thread-memory-store` capability)。
- 跨 tier 检索 SHALL 走 provider 能力路由;provider 不声明对应 tier 时 SHALL 返回结构化错误(说明后端不支持)。

新增行为:
- `memory_add(content, tags?, importance?, user_id?, scope?)` 新增可选 `scope` 参数,取值 `actor_scoped` (默认) / `workspace_shared`;`workspace_shared` 仅 workspace admin 角色可调用。语义由 `cross-thread-memory-store` capability 定义。
- `memory_search` 结果 SHALL 在元数据携带 `source_scope` 字段(`actor_scoped` / `workspace_shared` / `team_scoped` / `reflection` / `work_context_node`)。

#### Scenario: 跨层检索

- **WHEN** Agent 调用 `memory_search(query="报销政策")` 且 L3 与 L4 各有相关条目
- **THEN** 结果合并返回并标注各条目来源层(L3/L4),命中条目 access_count 递增

#### Scenario: 重复写入去重

- **WHEN** `memory_add` 的内容与该 Agent 既有 L4 条目规范化后相同
- **THEN** 不创建新条目,既有条目 access_count 递增并返回其 ID

#### Scenario: memory_search tier_2 行为不变

- **WHEN** Agent 调用 `memory_search(query="报销政策", top_k=5)`(不带 tier)
- **THEN** 行为与现状 byte-identical,仅检索 L3 + L4,结果按相关度排序

#### Scenario: memory_search tier_4 检索反思

- **WHEN** Agent 调用 `memory_search(query="如何等待表单加载", top_k=5, tier="tier_4")`
- **WHEN** provider 声明 tier-4
- **THEN** 返回 `status='approved'` 的反思条目(走 `reflection_search` 路径),结果元数据标注 `source_scope='reflection'`

#### Scenario: memory_search tier_5 检索跨线程记忆

- **WHEN** Agent 调用 `memory_search(query="团队共享的报销政策", top_k=5, tier="tier_5")`
- **THEN** 返回 workspace_shared 与 team_scoped 记忆,结果标注对应 `source_scope`

#### Scenario: provider 不支持 tier 时返回结构化错误

- **WHEN** Agent 调用 `memory_search(query=..., tier="tier_4")`
- **WHEN** 活动 provider 未声明 tier-4
- **THEN** 返回结构化错误(后端不支持 tier-4),检索不发生

#### Scenario: workspace_shared 写入需要 admin

- **WHEN** editor 角色调用 `memory_add(content='...', scope='workspace_shared')`
- **THEN** 返回结构化错误(权限不足),写入不发生

### Requirement: 弱检索升级门控

当 `memory_search` 或 `conversation_search` 返回空结果或全部结果相关度低于信号阈值时,系统 SHALL 在本轮后续上下文中注入一条升级提示(HintBlock),告知模型可以改写查询词、调整时间窗或使用 `exclude_session_ids`/游标再次检索。升级提示 SHALL:

- 每轮至多出现一次(防抖,复用 4.13 失败策略的 anti-thrash 语义);
- 位于 KV-cache 保护前缀区之后,计入预算记账;
- 受 4.13 执行器级失败策略约束(熔断/冷却下不再注入)。

#### Scenario: 空结果触发提示
- **WHEN** `memory_search` 返回空结果
- **THEN** 当轮后续上下文包含一条升级提示,说明可改写查询重试及可用迭代参数

#### Scenario: 提示防抖
- **WHEN** 同一轮内第二次记忆检索再次返回空结果
- **THEN** 不再注入第二条升级提示

#### Scenario: 充足结果不提示
- **WHEN** 检索返回相关度达阈值的结果
- **THEN** 不注入任何升级提示

### Requirement: 租户与作用域隔离

全部记忆工具 SHALL 以认证上下文中的 workspace 为第一层隔离,L1/L4 以 Agent 为作用域,L3 以用户为作用域;任何工具调用不得跨 workspace 读写记忆。

新增:
- 反思与 Work Context Graph 工具 SHALL 以 `workspace_id + actor_id` 为隔离(详见 `task-memory` capability);跨 workspace 调用 SHALL 返回结构化错误。
- Cross-thread 检索(`memory_search` tier=5)SHALL 遵守四层 namespace 隔离(详见 `cross-thread-memory-store` capability)。

#### Scenario: 跨 workspace 不可达

- **WHEN** workspace X 的 Agent 以 workspace Y 的记忆条目 ID 调用 `memory_update`
- **THEN** 返回"条目不存在"结构化错误,workspace Y 数据不变

#### Scenario: 跨 workspace 反思不可达

- **WHEN** workspace X 的 Agent 调用 `reflection_search(...)`
- **THEN** 结果集仅含 workspace X 的反思,workspace Y 的反思不返回

### Requirement: reflection_search 工具语义

`reflection_search(query, task_type?, top_k=5)` SHALL 在 provider 支持 tier-4 时返回匹配的反思条目:

- 仅返回 `status='approved'` 的反思;`pending / rejected / deprecated` 不返回。
- `task_type` 缺失时按 query 与 `use_cases` 字段做语义相似度匹配;非空时优先 `use_cases` 精确匹配。
- 返回字段:`reflection_id, title, hints, use_cases, confidence, source_episode_ids, version, created_at`。
- `hints` 字段 SHALL ≤ 300 词;超出 SHALL 拒绝该反思,不入库。

#### Scenario: 路径 a 检索注入

- **WHEN** Agent 调用 `reflection_search(query='如何抓取表单', task_type='expense_report_filing', top_k=3)`
- **WHEN** `reflections` 表有 2 条 `status='approved'` 且 `use_cases` 匹配 task_type 的反思
- **THEN** 工具返回这 2 条反思(≤ top_k),其中 `hints` 字段合计 ≤ 300 词;被注入 L1 `reflection_summary` block

#### Scenario: 跨 task_type 的语义检索

- **WHEN** Agent 调用 `reflection_search(query='异步任务调度', top_k=5)` 不带 task_type
- **THEN** 工具按 query 与 `use_cases` 做语义相似度匹配,返回 top-K 命中

#### Scenario: rejected / deprecated 不被消费

- **WHEN** `reflections` 表存在 `status='rejected'` 或 `status='deprecated'` 的反思
- **THEN** `reflection_search` 工具不返回这些条目

### Requirement: work_context_query 工具语义

`work_context_query(query, node_type?, top_k=5)` SHALL 在 provider 支持 tier-4 时返回匹配的 Work Context Graph 节点:

- `node_type` 可选,取值 `method | outcome | correction | source | pattern` 之一。
- 仅返回 `active=true` 的节点;`active=false`(被 superseded)不返回。
- 返回字段:`node_id, node_type, content, success_rate, usage_count, last_used_at, user_correction_count, source_reliability, linked_reflection_id`。

#### Scenario: 按 node_type 查询

- **WHEN** Agent 调用 `work_context_query(query='表单等待策略', node_type='method', top_k=3)`
- **WHEN** `work_context_nodes` 有 2 条 `active=true` 且 `node_type='method'` 命中
- **THEN** 工具返回这 2 条节点,字段含 success_rate / usage_count 等

#### Scenario: superseded 节点不返回

- **WHEN** 一个节点被其 reflection 的 superseded 关联切为 `active=false`
- **THEN** `work_context_query` 不返回该节点(但通过 `work_context_nodes` 表直接查询仍可见,用于审计)
