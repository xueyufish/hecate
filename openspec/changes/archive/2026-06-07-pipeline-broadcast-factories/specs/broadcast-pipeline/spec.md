## ADDED Requirements — 新增的需求

### Requirement: Broadcast pipeline factory function — 需求：广播管道工厂函数
系统应在 `engine/templates.py` 中提供 `build_broadcast_pipeline()` 工厂函数，接受参与者定义列表并返回一个 `GraphConfig`，表示一个顺序轮询图，所有参与者共享同一个 `messages` TOPIC 通道。

#### Scenario: Basic three-participant broadcast — 场景：基本的三参与者广播
- **WHEN — 当** 调用 `build_broadcast_pipeline(participants=[{"id": "alice", "model": "gpt-4o", "system_prompt": "..."}, {"id": "bob", "model": "gpt-4o", "system_prompt": "..."}, {"id": "charlie", "model": "gpt-4o", "system_prompt": "..."}])`
- **THEN — 则** 返回的 GraphConfig 应包含 3 个 AGENT 节点（alice、bob、charlie），所有节点都从同一个 `messages` TOPIC 通道读取和写入，顺序边形成 alice→bob→charlie→__end__

#### Scenario: Shared message visibility — 场景：共享消息可见性
- **WHEN — 当** 使用 N 个参与者创建广播管道
- **THEN — 则** `messages` TOPIC 通道应对所有参与者可读和可写，确保每个参与者看到所有先前参与者的所有消息

#### Scenario: Broadcast edge connectivity — 场景：广播边连接
- **WHEN — 当** 使用 N 个参与者创建广播管道
- **THEN — 则** 边应形成线性链：`__start__` → participant_0 → participant_1 → ... → participant_{N-1} → `__end__`，`entry` 字段应设置为 participant_0

### Requirement: Broadcast pipeline with moderator — 需求：带主持人的广播管道
系统应在 `build_broadcast_pipeline()` 中支持可选的主持人，在广播轮次前后参与，提供初始上下文和最终摘要。

#### Scenario: Broadcast with moderator enabled — 场景：启用主持人的广播
- **WHEN — 当** 调用 `build_broadcast_pipeline(participants=[...], moderator={"model": "gpt-4o", "system_prompt": "你是一名主持人。"})`
- **THEN — 则** 图应在开始和结束处包含一个额外的 `moderator` AGENT 节点：`__start__` → moderator → participant_0 → ... → participant_{N-1} → moderator → `__end__`，`entry` 字段应设置为 `moderator`

#### Scenario: Broadcast without moderator — 场景：无主持人的广播
- **WHEN — 当** 调用 `build_broadcast_pipeline(participants=[...])` 而不带主持人
- **THEN — 则** 图应仅包含参与者节点，无主持人节点

### Requirement: Broadcast participant validation — 需求：广播参与者验证
系统应在构建广播管道时验证参与者定义。

#### Scenario: Minimum participant count — 场景：最小参与者数量
- **WHEN — 当** 调用 `build_broadcast_pipeline(participants=[single_participant])` 且参与者少于 2 个
- **THEN — 则** 函数应引发 `ValueError` 并附带描述性消息

#### Scenario: Duplicate participant IDs rejected — 场景：拒绝重复的参与者 ID
- **WHEN — 当** 调用 `build_broadcast_pipeline(participants=[{"id": "agent", ...}, {"id": "agent", ...}])` 且参与者 ID 重复
- **THEN — 则** 函数应引发 `ValueError`

### Requirement: Broadcast pipeline JSON template — 需求：广播管道 JSON 模板
系统应在 `data/orchestration_templates/` 中包含一个 `broadcast-pipeline.json` 模板文件，演示带主持人的 3 参与者轮询广播。

#### Scenario: Template loads successfully — 场景：模板成功加载
- **WHEN — 当** 通过编排模板 API 加载 broadcast-pipeline 模板
- **THEN — 则** 模板应包含 3 个参与者 AGENT 节点、1 个主持人 AGENT 节点、所有节点共享的 `messages` TOPIC 通道，以及形成主持人在开始和结束处的轮询顺序边
