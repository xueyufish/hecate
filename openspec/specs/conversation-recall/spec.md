# conversation-recall Specification

## Purpose

定义会话召回存储(Conversation Recall Storage):把对话历史作为可语义检索的持久记忆层——回合提交时写时索引、按 workspace/agent/session 分层作用域、支持迭代式检索语义,并与 event retention 互不干涉,使 Agent 能跨会话检索过往对话。

## Requirements

### Requirement: 写时索引

当 `RECALL_INDEXING_ENABLED` 开启时,系统 SHALL 在回合提交时将会话中的 user 与 assistant 消息异步写入召回索引(元数据存储 + 向量索引):

- 仅索引 user/assistant 消息;工具结果、系统消息与推理内容 SHALL 排除;
- 索引幂等:同一消息(按内容哈希 + 会话 + 位置)不会被重复索引;
- 索引为尽力而为:向量化或写入失败不阻塞对话主路径,失败项 SHALL 可被补索引机制追平;
- 开关关闭时不产生任何索引写入,平台行为与合入前 byte-identical。

#### Scenario: 回合结束产生索引
- **WHEN** 一个包含 3 轮 user/assistant 交互的回合正常提交
- **THEN** 6 条消息异步进入召回索引,对话响应返回不被索引过程阻塞

#### Scenario: 工具结果不入索引
- **WHEN** 一轮对话包含大体积工具结果
- **THEN** 召回索引中只包含 user/assistant 消息,工具结果不可经 `conversation_search` 检出

#### Scenario: 索引失败可追平
- **WHEN** 某回合的向量化调用失败
- **THEN** 对话不受影响;补索引机制在后续将该回合消息补齐入库

#### Scenario: 关闭时零写入
- **WHEN** `RECALL_INDEXING_ENABLED=false`
- **WHEN** 多轮对话正常进行
- **THEN** 召回索引无任何新增记录

### Requirement: 召回作用域与租户隔离

每条召回记录 SHALL 携带 workspace、agent、conversation、session 标识(可获得时含 user 标识)。`conversation_search` SHALL 仅返回与调用方作用域匹配的记录:

- workspace 隔离为一等公民:跨 workspace 的召回不可达;
- 默认检索范围为当前 Agent 的召回;不跨 Agent 扩散(跨 Agent 检索留待后续特性)。

#### Scenario: workspace 之间不可见
- **WHEN** workspace X 的 Agent 调用 `conversation_search`
- **THEN** 结果仅来自 workspace X 的索引记录,workspace Y 的会话内容不可检出

#### Scenario: 默认 Agent 范围
- **WHEN** 同一 workspace 内 Agent A 调用 `conversation_search`
- **THEN** Agent B 的会话内容不出现在结果中

### Requirement: conversation_search 检索语义

`conversation_search` SHALL 提供如下调用面:

- `query`(必填)、`limit`(缺省 5,上限 20)、`start_date`/`end_date`(时间窗过滤)、`roles`(角色过滤,缺省 user+assistant)、`cursor`(游标分页)、`exclude_session_ids`(迭代排除已检视会话);
- 结果为消息级摘录,每条包含:原文内容、所属 conversation/session 标识、消息角色、时间戳、相关度得分;
- 相关度低于信号阈值或无结果时,返回结构化空结果(触发 `agent-memory-tools` 定义的升级门控);
- 检索结果作为工具结果进入上下文,SHALL 遵循既有工具结果安全通路(guardrail/中间件),无任何旁路。

#### Scenario: 基础语义检索
- **WHEN** Agent 调用 `conversation_search(query="用户上次提到的部署问题")`
- **THEN** 返回按相关度排序的历史消息摘录,每条含会话标识与时间戳

#### Scenario: 时间窗与角色过滤
- **WHEN** Agent 以 `start_date`/`end_date` 与 `roles=["user"]` 调用
- **THEN** 仅返回时间窗内 user 角色的消息

#### Scenario: 迭代排除
- **WHEN** Agent 以 `exclude_session_ids=[s1]` 再次检索
- **THEN** 会话 s1 的消息不再出现在结果中

#### Scenario: 游标分页
- **WHEN** 首次检索结果附带游标且 Agent 携带游标再次调用
- **THEN** 返回后续批次且与前次结果无重复

### Requirement: 与 event retention 互不干涉

召回索引 SHALL 独立于事件留存策略:event retention 的清理不作用于召回索引,召回层是超越事件生命周期的持久归档。删除会话/会话所属 conversation 时 SHALL 级联清理其召回记录;召回索引自身的保留策略 SHALL 可配置(缺省长期保留,支持按 workspace 配置 TTL)。

#### Scenario: 事件清理不损伤召回
- **WHEN** event retention 按策略清理某会话的执行事件
- **THEN** 该会话消息仍可被 `conversation_search` 检出

#### Scenario: conversation 删除级联
- **WHEN** 某 conversation 被删除
- **THEN** 其召回记录从索引中移除,不再可检出

### Requirement: 召回层元数据可观测

召回索引 SHALL 暴露最小可观测面:索引条目计数(按 workspace/agent 分组)、最近索引时间、补索引队列深度,供运维查询;不要求本期提供 studio UI。

#### Scenario: 运维查询索引状态
- **WHEN** 管理员查询召回索引状态
- **THEN** 可获得按 workspace/agent 的条目计数、最近索引时间与待补索引数量