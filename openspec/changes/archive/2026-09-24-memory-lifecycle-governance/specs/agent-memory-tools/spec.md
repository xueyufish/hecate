# agent-memory-tools Specification(Delta)

## MODIFIED Requirements

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
