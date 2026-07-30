## ADDED Requirements — 新增的需求

### Requirement: Sequential pipeline factory function — 需求：顺序管道工厂函数
系统应在 `engine/templates.py` 中提供 `build_sequential_pipeline()` 工厂函数，接受阶段定义列表并返回表示带自动接线通道的线性顺序管道的 `GraphConfig`。

#### Scenario: Basic two-stage pipeline — 场景：基本的两阶段管道
- **WHEN — 当** 调用 `build_sequential_pipeline(stages=[{"id": "researcher", "model": "gpt-4o", "system_prompt": "你是一名研究员。"}, {"id": "writer", "model": "gpt-4o", "system_prompt": "你是一名作家。"}])`
- **THEN — 则** 返回的 GraphConfig 应包含 2 个 AGENT 节点（researcher、writer），两个阶段均可读可写的共享 `messages` TOPIC 通道，以及 researcher 可写、writer 可读的 `researcher_output` LAST_VALUE 通道

#### Scenario: Three-stage pipeline with inter-stage data flow — 场景：带阶段间数据流的三阶段管道
- **WHEN — 当** 调用 `build_sequential_pipeline(stages=[{"id": "a", ...}, {"id": "b", ...}, {"id": "c", ...}])`
- **THEN — 则** 阶段 A 应写入通道 `messages` 和 `a_output`，阶段 B 应从 `messages` 和 `a_output` 读取并写入 `messages` 和 `b_output`，阶段 C 应从 `messages` 和 `b_output` 读取并写入 `messages`

#### Scenario: Pipeline edge connectivity — 场景：管道边连接
- **WHEN — 当** 使用 N 个阶段创建顺序管道
- **THEN — 则** 边应形成线性链：`__start__` → stage_0 → stage_1 → ... → stage_{N-1} → `__end__`，`entry` 字段应设置为 stage_0

#### Scenario: Pipeline channel auto-wiring — 场景：管道通道自动接线
- **WHEN — 当** 创建顺序管道
- **THEN — 则** `messages` TOPIC 通道应对所有阶段可读和可写，每个阶段 N 应有专用的 `{stage_id}_output` LAST_VALUE 通道，对阶段 N 可写并对阶段 N+1（如果存在）可读

### Requirement: Sequential pipeline with revision loop — 需求：带修订循环的顺序管道
系统应在 `build_sequential_pipeline()` 中支持可选的修订配置，追加 CONDITION 节点并从最后阶段创建回指定修订目标的修订循环。

#### Scenario: Pipeline with revision loop enabled — 场景：启用修订循环的管道
- **WHEN — 当** 调用 `build_sequential_pipeline(stages=[...], revision_config={"expression": "quality == 'needs_revision'", "target_stage": "writer"})`
- **THEN — 则** 图应在最后阶段后包含一个 CONDITION 节点，条件边在表达式评估为真时路由到 `target_stage`，在假时路由到 `__end__`

#### Scenario: Pipeline without revision loop — 场景：无修订循环的管道
- **WHEN — 当** 调用 `build_sequential_pipeline(stages=[...])` 而不带 `revision_config`
- **THEN — 则** 图应不包含 CONDITION 节点，并严格线性从第一阶段到 `__end__`

### Requirement: Sequential pipeline stage validation — 需求：顺序管道阶段验证
系统应在构建顺序管道时验证阶段定义。

#### Scenario: Minimum stage count — 场景：最小阶段数量
- **WHEN — 当** 调用 `build_sequential_pipeline(stages=[single_stage])` 且阶段少于 2 个
- **THEN — 则** 函数应引发 `ValueError` 并附带描述性消息

#### Scenario: Duplicate stage IDs rejected — 场景：拒绝重复的阶段 ID
- **WHEN — 当** 调用 `build_sequential_pipeline(stages=[{"id": "agent", ...}, {"id": "agent", ...}])` 且阶段 ID 重复
- **THEN — 则** 函数应引发 `ValueError`

### Requirement: Sequential pipeline JSON template — 需求：顺序管道 JSON 模板
系统应在 `data/orchestration_templates/` 中包含一个 `sequential-pipeline.json` 模板文件，演示带修订循环的 3 阶段 researcher→writer→reviewer 管道。

#### Scenario: Template loads successfully — 场景：模板成功加载
- **WHEN — 当** 通过编排模板 API 加载 sequential-pipeline 模板
- **THEN — 则** 模板应包含 3 个 AGENT 节点、1 个 CONDITION 节点、一个 `messages` TOPIC 通道，以及具有正确可读/可写接线的每阶段 LAST_VALUE 通道
