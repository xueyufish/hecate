# agent-memory-tools Specification

## Purpose

定义 Agent 可调用的记忆工具面:记忆编辑(L1 记忆块精确/整块操作)、事实记忆检索与修正(L3/L4,带 revision 乐观并发与审计)、弱检索升级门控,以及工具注册、开关与租户隔离的外部可观察行为。

## Requirements

### Requirement: 记忆工具注册与开关

系统 SHALL 注册 8 个记忆类内置工具:`memory_replace` / `memory_insert` / `memory_rethink`(L1 记忆块编辑)、`memory_search` / `memory_add` / `memory_update` / `memory_forget`(L3/L4 事实记忆)、`conversation_search`(会话召回,行为由 `conversation-recall` capability 定义)。工具 SHALL 经既有内置工具 seeding 通路注册,Agent 按名称挂载。

- `MEMORY_TOOLS_ENABLED` 默认关闭;关闭时工具不出现在 seeding 集合,平台行为与该变更合入前 byte-identical。
- `conversation_search` 额外受 `RECALL_INDEXING_ENABLED` 约束。

#### Scenario: flag 关闭时平台行为不变
- **WHEN** `MEMORY_TOOLS_ENABLED=false`(默认)
- **WHEN** 平台启动并 seeding 内置工具
- **THEN** 记忆工具不出现在工具注册表,Agent 无法挂载,既有工具行为不变

#### Scenario: 开启后可挂载
- **WHEN** `MEMORY_TOOLS_ENABLED=true`
- **WHEN** Agent 配置引用记忆工具名称
- **THEN** 工具出现在 Agent 的工具列表并可被模型调用

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

- `memory_search(query, top_k, tags?)`:在当前 workspace 范围内对 L3 用户记忆与 L4 知识记忆执行混合检索,结果按相关度排序并标注来源层(L3/L4);检索命中 SHALL 递增对应条目的 access_count。
- `memory_add(content, tags?, importance?, user_id?)`:向 L4 知识记忆写入新条目;与既有条目内容规范化后重复时,SHALL 更新既有条目(递增 access_count)而非创建重复。
- `memory_update(memory_id, ...)` / `memory_forget(memory_id)`:修正/软删除 L3 或 L4 条目,遵循 revision 乐观并发;`memory_forget` 为软删除(记录保留、不可检索)。

#### Scenario: 跨层检索
- **WHEN** Agent 调用 `memory_search(query="报销政策")` 且 L3 与 L4 各有相关条目
- **THEN** 结果合并返回并标注各条目来源层(L3/L4),命中条目 access_count 递增

#### Scenario: 重复写入去重
- **WHEN** `memory_add` 的内容与该 Agent 既有 L4 条目规范化后相同
- **THEN** 不创建新条目,既有条目 access_count 递增并返回其 ID

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

全部记忆工具 SHALL 以认证上下文中的 workspace 为第一层隔离,L1/L4 以 Agent 为作用域,L3 以用户为作用域;任何工具调用不得跨 workspace 读写记忆。会话召回工具的隔离由 `conversation-recall` capability 定义。

#### Scenario: 跨 workspace 不可达
- **WHEN** workspace X 的 Agent 以 workspace Y 的记忆条目 ID 调用 `memory_update`
- **THEN** 返回"条目不存在"结构化错误,workspace Y 数据不变