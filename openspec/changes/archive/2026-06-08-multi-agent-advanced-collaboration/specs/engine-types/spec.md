## 修改的需求

### 需求：NodeType 枚举定义 6 种执行行为
`NodeType` 枚举应定义：CONVERSATION、TOOL_CALL、CONDITION、AGENT、KNOWLEDGE_RETRIEVAL、VARIABLE_SET、FAN_OUT、MERGE。

`CollaborationEventType` 枚举应在 `engine/eventbus.py` 中定义，包含值：AGENT_MESSAGE、AGENT_REQUEST、AGENT_RESPONSE、TASK_ASSIGNED、TASK_COMPLETED、NEGOTIATION_PROPOSAL、NEGOTIATION_ACCEPT、NEGOTIATION_REJECT、DEBATE_ARGUMENT、DEBATE_REBUTTAL、DEBATE_CONCLUSION。

这是一个与 `engine/eventstore.py` 中现有 `EventType` 并列的新枚举——不应修改现有的 EventType。

#### 场景：对话节点
- **当** 一个节点的类型为 CONVERSATION
- **则** 工作者应使用当前通道状态调用 LLM

#### 场景：智能体节点
- **当** 一个节点的类型为 AGENT
- **则** 工作者应将执行委托给代表另一个智能体的子图

#### 场景：协作事件类型
- **当** 引用 `CollaborationEventType.AGENT_MESSAGE`
- **则** 它应等于字符串 `"AGENT_MESSAGE"`
