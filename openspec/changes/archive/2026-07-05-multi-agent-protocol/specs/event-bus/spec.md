## ADDED Requirements — 新增需求

### Requirement: CollaborationEventType 支持 A2A 特定事件 — CollaborationEventType supports A2A-specific events
引擎应在 `CollaborationEventType` 枚举中扩展特定于 A2A 的值：`A2A_TASK_DELEGATED`、`A2A_TASK_RECEIVED`、`A2A_ARTIFACT_SENT`、`A2A_ARTIFACT_RECEIVED`、`A2A_AGENT_DISCOVERED`。

#### Scenario: 发布 A2A 任务委托事件 — Publish A2A task delegated event
- **WHEN** A2A 客户端将任务委托给远程 Agent
- **THEN** EventBus 应支持发布 `event_type: CollaborationEventType.A2A_TASK_DELEGATED` 的事件，负载中包含远程 Agent URL 和任务 ID

#### Scenario: 订阅 A2A artifact 事件 — Subscribe to A2A artifact events
- **WHEN** 处理器订阅主题 `"a2a_artifacts"`
- **THEN** 当来自远程 A2A Agent 的 artifacts 到达时，处理器应接收 `event_type: CollaborationEventType.A2A_ARTIFACT_RECEIVED` 的事件

### Requirement: EventBus 支持 A2A 任务关联 — EventBus supports A2A task correlation
EventBus 应允许将 A2A 任务 ID 与本地协作主题关联，使事件处理器能够按 A2A 任务上下文过滤事件。

#### Scenario: 将 A2A 任务与本地主题关联 — Correlate A2A task with local topic
- **WHEN** 接收到 ID 为 `task-123` 的 A2A 任务，且本地执行向主题 `"agent_worker"` 发布事件
- **THEN** 事件的负载元数据中应包含 `a2a_task_id: "task-123"`，允许进行关联查询
