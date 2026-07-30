## ADDED Requirements — 新增需求

### Requirement: 路由器根据所需模态过滤候选模型 — Router filters candidates by required modalities
智能路由器应在应用成本/延迟路由策略前，根据请求所需的输入模态（例如，图像输入需要 `modalities.input` 包含 `"image"`）过滤模型候选。

#### Scenario: 将图像输入路由到支持视觉的模型 — Route image input to vision-capable model
- **WHEN** 请求包含图像内容部分，路由器的候选模型为 `[gpt-4o (vision: true), text-only-model (vision: false)]`
- **THEN** 路由器应将 `text-only-model` 从候选列表中排除，并路由到 `gpt-4o`

#### Scenario: 没有可用的合适模型 — No capable model available
- **WHEN** 请求需要音频输入，但没有候选模型的 `modalities.input` 包含 `"audio"`
- **THEN** 路由器应返回 `NoCapableModelError`，列出所需的模态

### Requirement: 路由器考虑工具调用请求的能力标志 — Router considers capability flags for tool-calling requests
当请求包含工具定义时，智能路由器应优先选择 `capabilities.tool_call: true` 的模型，避免无法执行工具的模型。

#### Scenario: 将工具调用请求路由到具备能力的模型 — Route tool-calling request to capable model
- **WHEN** 请求包含 `tools` 定义，路由器的候选模型具有混合的 `tool_call` 能力
- **THEN** 路由器应在应用成本/延迟策略前，仅过滤出 `tool_call: true` 的候选模型
