## ADDED Requirements — 新增需求

### Requirement: CollaborationPattern enum — CollaborationPattern 枚举
引擎应在 `engine/patterns.py` 中定义一个 `CollaborationPattern` StrEnum，包含 6 个值：`SEQUENTIAL`、`PARALLEL`、`HANDOFF`、`BROADCAST`、`NEGOTIATION`、`DEBATE`。每个值映射到规范的协作拓扑。

#### Scenario: Enum values are accessible — 枚举值可访问
- **当** 执行 `from hecate.engine.patterns import CollaborationPattern`
- **则** 枚举应恰好包含 6 个成员：SEQUENTIAL、PARALLEL、HANDOFF、BROADCAST、NEGOTIATION、DEBATE

#### Scenario: Enum values are lowercase strings — 枚举值为小写字符串
- **当** 访问 `CollaborationPattern.SEQUENTIAL.value`
- **则** 应返回 `"sequential"`

### Requirement: Pattern inference from graph structure — 从图谱结构进行模式推断
系统应提供一个 `infer_pattern(config: GraphConfig) -> CollaborationPattern | None` 函数，分析图谱拓扑以检测协作模式。如果没有已知模式匹配，该函数返回 `None`。

#### Scenario: Sequential pattern detected — 检测到 Sequential 模式
- **当** `infer_pattern()` 收到一个 GraphConfig，包含由无条件边连接的 3 个以上 AGENT 节点的线性链，无 FAN_OUT/MERGE 节点，无 handoff 边
- **则** 应返回 `CollaborationPattern.SEQUENTIAL`

#### Scenario: Parallel pattern detected — 检测到 Parallel 模式
- **当** `infer_pattern()` 收到一个 GraphConfig，包含至少一个 FAN_OUT 节点和一个 MERGE 节点
- **则** 应返回 `CollaborationPattern.PARALLEL`

#### Scenario: Handoff pattern detected — 检测到 Handoff 模式
- **当** `infer_pattern()` 收到一个 GraphConfig，其中所有边的 `trigger="handoff"`
- **则** 应返回 `CollaborationPattern.HANDOFF`

#### Scenario: Broadcast pattern detected — 检测到 Broadcast 模式
- **当** `infer_pattern()` 收到一个 GraphConfig，其中所有 agent 节点共享对单个 TOPIC 通道的读/写访问，且边形成无 FAN_OUT/MERGE 的顺序链
- **则** 应返回 `CollaborationPattern.BROADCAST`

#### Scenario: Negotiation pattern detected — 检测到 Negotiation 模式
- **当** `infer_pattern()` 收到一个 GraphConfig，包含恰好 2 个 agent 节点、一个检查协议状态的条件节点，以及从响应者回到提议者的循环边
- **则** 应返回 `CollaborationPattern.NEGOTIATION`

#### Scenario: Debate pattern detected — 检测到 Debate 模式
- **当** `infer_pattern()` 收到一个 GraphConfig，包含 2 个以上 agent 节点、一个轮次计数器 ACCUMULATOR 通道，以及形成循环的交替执行边
- **则** 应返回 `CollaborationPattern.DEBATE`

#### Scenario: Unknown pattern returns None — 未知模式返回 None
- **当** `infer_pattern()` 收到一个与任何已知模式都不匹配的 GraphConfig
- **则** 应返回 `None`

### Requirement: Pattern-to-graph builder — 模式到图谱构建器
系统应提供一个 `build_graph_from_pattern(pattern: CollaborationPattern, config: dict) -> GraphConfig` 函数，从模式类型和配置参数生成完整的 GraphConfig。

#### Scenario: Build sequential graph — 构建 Sequential 图谱
- **当** 调用 `build_graph_from_pattern(CollaborationPattern.SEQUENTIAL, {"stages": [{"name": "researcher", "model": "gpt-4o", "prompt": "..."}]})`
- **则** 应返回一个 GraphConfig，包含在线性链中连接的 AGENT 节点、用于消息的 TOPIC 通道，以及入口点为第一个阶段

#### Scenario: Build parallel graph — 构建 Parallel 图谱
- **当** 调用 `build_graph_from_pattern(CollaborationPattern.PARALLEL, {"coordinator": {...}, "workers": [{...}, {...}], "aggregator": {...}})` 
- **则** 应返回一个 GraphConfig，包含 coordinator AGENT → FAN_OUT → worker AGENT 节点 → MERGE → aggregator AGENT

#### Scenario: Build handoff graph — 构建 Handoff 图谱
- **当** 调用 `build_graph_from_pattern(CollaborationPattern.HANDOFF, {"router": {...}, "specialists": [{...}, {...}]})`
- **则** 应返回一个 GraphConfig，包含通过 handoff 边（`trigger="handoff"`）连接到 specialist AGENT 节点的 router AGENT

#### Scenario: Build broadcast graph — 构建 Broadcast 图谱
- **当** 调用 `build_graph_from_pattern(CollaborationPattern.BROADCAST, {"participants": [{...}, {...}], "moderator": {...}})` 
- **则** 应返回一个 GraphConfig，所有参与者共享一个 TOPIC 通道，参与者之间为顺序边，末尾有可选的 moderator

#### Scenario: Build negotiation graph — 构建 Negotiation 图谱
- **当** 调用 `build_graph_from_pattern(CollaborationPattern.NEGOTIATION, {"proposer": {...}, "responder": {...}, "max_rounds": 5})`
- **则** 应返回一个 GraphConfig，包含 proposer AGENT → responder AGENT → 检查协议的条件节点 → 循环回 proposer 或结束

#### Scenario: Build debate graph — 构建 Debate 图谱
- **当** 调用 `build_graph_from_pattern(CollaborationPattern.DEBATE, {"debater_a": {...}, "debater_b": {...}, "judge": {...}, "rounds": 3})`
- **则** 应返回一个 GraphConfig，debater_a → debater_b 通过带轮次计数器的循环交替进行，末尾有可选的 judge AGENT

### Requirement: Pattern metadata endpoint — 模式元数据端点
系统应暴露 `GET /api/collaboration-patterns`，返回所有 6 种模式定义的列表，包含名称、描述、必需参数和预览元数据。

#### Scenario: List all patterns — 列出所有模式
- **当** 使用有效 API 密钥调用 `GET /api/collaboration-patterns`
- **则** 响应应包含一个 `items` 数组，恰好 6 个条目，每个条目包含 `id`、`name`、`description`、`parameters`（配置的 JSON Schema）和 `preview`（节点数量、边数量估算）

#### Scenario: Pattern parameter schemas — 模式参数模式
- **当** 从响应中检查 sequential 模式条目
- **则** 其 `parameters` 应是一个描述必需字段的 JSON Schema 对象：`stages`（包含 `name`、`model`、`prompt` 的对象数组）

### Requirement: Pattern graph generation endpoint — 模式图谱生成端点
系统应暴露 `POST /api/collaboration-patterns/{pattern}/generate`，接受模式配置并返回完整的 Graph DSL JSON。

#### Scenario: Generate sequential graph via API — 通过 API 生成 Sequential 图谱
- **当** 使用 `{"stages": [{"name": "step1", "model": "gpt-4o", "prompt": "You are step 1."}, {"name": "step2", "model": "gpt-4o", "prompt": "You are step 2."}]}` 调用 `POST /api/collaboration-patterns/sequential/generate`
- **则** 响应应是一个有效的 Graph DSL JSON，包含 2 个 AGENT 节点、顺序边、一个 TOPIC `messages` 通道，入口点为 `step1`

#### Scenario: Generate with invalid pattern — 使用无效模式生成
- **当** 调用 `POST /api/collaboration-patterns/unknown/generate`
- **则** API 应返回 422，错误详情指示模式无效

#### Scenario: Generate with missing required parameter — 缺少必需参数时生成
- **当** 使用 `{}`（缺少 stages）调用 `POST /api/collaboration-patterns/sequential/generate`
- **则** API 应返回 422，验证错误指示 `stages` 是必需的

### Requirement: Negotiation and debate JSON templates — Negotiation 和 Debate JSON 模板
系统应将 `negotiation.json` 和 `debate.json` 模板文件包含在 `data/orchestration_templates/` 中，由模板加载系统自动发现。

#### Scenario: Negotiation template in catalog — 目录中的 Negotiation 模板
- **当** 调用 `GET /api/orchestration-templates`
- **则** 响应应包含 `id` 为 "negotiation" 的条目

#### Scenario: Debate template in catalog — 目录中的 Debate 模板
- **当** 调用 `GET /api/orchestration-templates`
- **则** 响应应包含 `id` 为 "debate" 的条目
