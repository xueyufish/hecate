## MODIFIED Requirements — 修改的需求

### Requirement：EnginePort 抽象接口将引擎与服务解耦 — EnginePort 抽象接口将引擎与服务解耦
`EnginePort` ABC SHALL 定义 7 个抽象方法和 4 个可选方法，引擎调用这些方法进行所有 I/O 操作，不从 services 层导入。此外，它 SHALL 暴露一个可选的 `event_store` 属性用于事件持久化。

#### Scenario：LLM 调用
- **WHEN** 调用 `llm_invoke(messages, config)`
- **THEN** 它 SHALL 返回一个产出 token 的 `AsyncGenerator[str, None]`

#### Scenario：工具执行
- **WHEN** 调用 `tool_execute(name, args, context)`
- **THEN** 它 SHALL 返回工具的结果（类型取决于工具）

#### Scenario：知识库查询
- **WHEN** 调用 `knowledge_query(query, kb_ids)`
- **THEN** 它 SHALL 返回包含内容和元数据的文档块字典列表

#### Scenario：Checkpoint 保存
- **WHEN** 调用 `checkpoint_save(state)`
- **THEN** 它 SHALL 持久化状态并返回 UUID checkpoint ID

#### Scenario：Checkpoint 加载
- **WHEN** 调用 `checkpoint_load(checkpoint_id)`
- **THEN** 它 SHALL 返回 checkpoint 的状态字典

#### Scenario：对话加载
- **WHEN** 调用 `conversation_load(session_id)`
- **THEN** 它 SHALL 按时间顺序返回消息字典列表

#### Scenario：对话保存
- **WHEN** 调用 `conversation_save(session_id, messages)`
- **THEN** 它 SHALL 持久化对话消息

#### Scenario：EventStore 属性
- **WHEN** EnginePort 有 event_store 设置
- **THEN** `port.event_store` SHALL 返回一个 `EventStore` 实例