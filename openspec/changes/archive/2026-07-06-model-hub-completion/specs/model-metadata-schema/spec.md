## ADDED Requirements — 新增需求

### Requirement: ModelRegistryModel 存储结构化模型元数据 — ModelRegistryModel stores structured model metadata
系统应在 `ModelRegistryModel` 上添加 `model_metadata` JSON 列，包含 `modalities`（输入/输出数组）、`capabilities`（布尔标志）和 `limits`（上下文/输出整数）。

#### Scenario: 多模态模型元数据 — Multi-modal model metadata
- **WHEN** 像 GPT-4o 这样的模型注册时带有 `model_metadata: {modalities: {input: ["text", "image", "audio"], output: ["text"]}, capabilities: {reasoning: true, tool_call: true, vision: true}, limits: {context: 128000, output: 16384}}`
- **THEN** 系统应存储并提供此元数据，用于路由、目录显示和能力过滤

#### Scenario: 纯文本模型元数据 — Text-only model metadata
- **WHEN** 像 text-embedding-ada-002 这样的模型注册时带有 `model_metadata: {modalities: {input: ["text"], output: ["embedding"]}, capabilities: {}, limits: {context: 8192}}`
- **THEN** 系统应正确识别其为嵌入模型，不适合聊天

### Requirement: 系统将现有 model_type 迁移到 model_metadata — System migrates existing model_type to model_metadata
系统应在迁移期间根据当前的 `model_type` 值，为现有行填充 `model_metadata`，使用保守的默认值。

#### Scenario: 迁移聊天模型 — Migrate chat models
- **WHEN** 现有模型的 `model_type = "chat"`
- **THEN** 迁移应设置 `model_metadata = {modalities: {input: ["text"], output: ["text"]}, capabilities: {tool_call: false}, limits: {}}`

#### Scenario: 迁移嵌入模型 — Migrate embedding models
- **WHEN** 现有模型的 `model_type = "embedding"`
- **THEN** 迁移应设置 `model_metadata = {modalities: {input: ["text"], output: ["embedding"]}, capabilities: {}, limits: {}}`

### Requirement: 系统提供向后兼容的 model_type 访问器 — System provides backward-compatible model_type accessor
系统应从 `model_metadata` 计算 `model_type`，以向后兼容读取 `model_type` 字段的现有代码。

#### Scenario: 从元数据派生聊天类型 — Derive chat type from metadata
- **WHEN** `model_metadata.modalities.output` 包含 `"text"` 且 `input` 仅包含 `"text"`
- **THEN** 计算出的 `model_type` 应为 `"chat"`

#### Scenario: 从元数据派生嵌入类型 — Derive embedding type from metadata
- **WHEN** `model_metadata.modalities.output` 包含 `"embedding"`
- **THEN** 计算出的 `model_type` 应为 `"embedding"`

### Requirement: 目录显示来自 model_metadata 的能力徽章 — Catalog displays capability badges from model_metadata
系统应基于 `model_metadata.capabilities` 和 `model_metadata.modalities`，在 Model Catalog UI 中渲染能力徽章（vision、tool_call、reasoning、streaming）。

#### Scenario: 多模态模型的视觉徽章 — Vision badge for multi-modal model
- **WHEN** 模型的 `capabilities.vision: true` 或 `modalities.input` 包含 `"image"`
- **THEN** 目录应显示视觉能力徽章

#### Scenario: 上下文窗口显示 — Context window display
- **WHEN** 模型的 `limits.context: 128000`
- **THEN** 目录应显示 "128K context" 徽章
