## ADDED Requirements — 新增需求

### Requirement: Admin can test model with custom prompt — 管理员可以使用自定义提示词测试模型
The system SHALL provide a model debugging endpoint that sends a test prompt to a selected model and returns the response.

系统应提供一个模型调试端点，向所选模型发送测试提示词并返回响应。

#### Scenario: Test model with valid parameters — 使用有效参数测试模型
- **WHEN** admin calls POST /api/models/test with model_id, prompt, temperature=0.7, max_tokens=100
- **THEN** system calls the model via llm_service.chat() and returns the response content, model used, and token usage
- **当**管理员调用 POST /api/models/test，参数包含 model_id、prompt、temperature=0.7、max_tokens=100
- **则**系统通过 llm_service.chat() 调用模型，并返回响应内容、使用的模型和 token 用量

#### Scenario: Test model with invalid model — 使用无效模型测试
- **WHEN** admin calls POST /api/models/test with a model that is not available
- **THEN** system returns 400 error with the LiteLLM error message
- **当**管理员调用 POST /api/models/test，使用不可用的模型
- **则**系统返回 400 错误，附带 LiteLLM 错误消息

### Requirement: Model debugging UI provides parameter controls — 模型调试 UI 提供参数控制
The frontend SHALL provide a testing panel with model selection, prompt input, parameter sliders, and response display.

前端应提供一个测试面板，包含模型选择、提示词输入、参数滑块和响应显示。

#### Scenario: Use model debugging panel — 使用模型调试面板
- **WHEN** admin opens the model debugging page
- **THEN** page shows: model dropdown (grouped by provider), prompt textarea, temperature slider (0-2), max_tokens input, "Test" button, and response area
- **当**管理员打开模型调试页面
- **则**页面显示：模型下拉框（按 provider 分组）、提示词文本域、temperature 滑块（0-2）、max_tokens 输入框、"测试"按钮和响应区域
