## MODIFIED Requirements — 修改后的需求

### Requirement: Factory functions exported from templates module — 需求：从模板模块导出的工厂函数
The system SHALL export `build_sequential_pipeline`, `build_broadcast_pipeline`, `build_negotiation_graph`, and `build_debate_graph` from `engine/templates.py`. Additionally, `build_negotiation_graph` and `build_debate_graph` SHALL have corresponding JSON template files for catalog listing.

系统应从 `engine/templates.py` 导出 `build_sequential_pipeline`、`build_broadcast_pipeline`、`build_negotiation_graph` 和 `build_debate_graph`。此外，`build_negotiation_graph` 和 `build_debate_graph` 应有对应的 JSON 模板文件用于目录列表。

#### Scenario: Import negotiation graph factory — 场景：导入协商图工厂
- **WHEN** `from hecate.engine.templates import build_negotiation_graph` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable

- **当**执行 `from hecate.engine.templates import build_negotiation_graph`
- **则**导入应成功且函数应可调用

#### Scenario: Import debate graph factory — 场景：导入辩论图工厂
- **WHEN** `from hecate.engine.templates import build_debate_graph` is executed
- **THEN** the import SHALL succeed and the function SHALL be callable

- **当**执行 `from hecate.engine.templates import build_debate_graph`
- **则**导入应成功且函数应可调用

## ADDED Requirements — 新增需求

### Requirement: Orchestration template listing includes pattern type — 需求：编排模板列表包含模式类型
The `GET /api/orchestration-templates` endpoint SHALL include a `pattern_type` field in each template item, inferred from the template's graph structure using `infer_pattern()`.

`GET /api/orchestration-templates` 端点应在每个模板项中包含 `pattern_type` 字段，该字段通过 `infer_pattern()` 从模板的图形结构推断得出。

#### Scenario: Sequential template has pattern_type — 场景：顺序模板包含 pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `sequential-pipeline` item SHALL have `pattern_type` set to `"sequential"`

- **当**调用 `GET /api/orchestration-templates`
- **则** `sequential-pipeline` 项的 `pattern_type` 应设置为 `"sequential"`

#### Scenario: Fan-out template has pattern_type — 场景：扇出模板包含 pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `fan-out-pipeline` item SHALL have `pattern_type` set to `"parallel"`

- **当**调用 `GET /api/orchestration-templates`
- **则** `fan-out-pipeline` 项的 `pattern_type` 应设置为 `"parallel"`

#### Scenario: Customer service template has pattern_type — 场景：客服模板包含 pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `customer-service-triage` item SHALL have `pattern_type` set to `"handoff"`

- **当**调用 `GET /api/orchestration-templates`
- **则** `customer-service-triage` 项的 `pattern_type` 应设置为 `"handoff"`

#### Scenario: Broadcast template has pattern_type — 场景：广播模板包含 pattern_type
- **WHEN** `GET /api/orchestration-templates` is called
- **THEN** the `broadcast-pipeline` item SHALL have `pattern_type` set to `"broadcast"`

- **当**调用 `GET /api/orchestration-templates`
- **则** `broadcast-pipeline` 项的 `pattern_type` 应设置为 `"broadcast"`

#### Scenario: Template with no matching pattern — 场景：无匹配模式的模板
- **WHEN** a template's graph structure does not match any known pattern
- **THEN** its `pattern_type` SHALL be `null`

- **当**模板的图形结构不匹配任何已知模式
- **则**其 `pattern_type` 应为 `null`
