## ADDED Requirements — 新增需求

### Requirement: Streaming Response Display — 需求：流式响应显示
模型调试页面应通过 `/v1/chat/completions` 使用 `stream: true` 支持流式响应。响应内容应在分块到达时逐步显示，流式进行中显示输入指示器。

#### Scenario: Streaming response — 场景：流式响应
- **WHEN** 用户点击"Run Test"，流式已启用
- **THEN** 响应区域应逐步显示内容，直至完成前显示输入指示器

#### Scenario: Non-streaming fallback — 场景：非流式回退
- **WHEN** 流式被禁用或失败
- **THEN** 系统应回退到非流式模式并显示完整响应

### Requirement: System Prompt Support — 需求：系统提示词支持
模型调试页面应提供系统提示词输入字段。当提供时，系统消息应作为 `/v1/chat/completions` 请求中的第一条消息包含在内。

#### Scenario: Test with system prompt — 场景：使用系统提示词测试
- **WHEN** 用户输入系统提示词并运行测试
- **THEN** 请求应包含 `{"role": "system", "content": "..."}` 作为第一条消息

#### Scenario: Empty system prompt — 场景：空的系统提示词
- **WHEN** 系统提示词字段为空
- **THEN** 请求应仅包含用户消息

### Requirement: Response Time Measurement — 需求：响应时间测量
模型调试页面应测量并显示：
- **首 Token 时间（TTFT）** — 从请求开始到首个内容分块到达的毫秒数
- **总时间** — 从请求开始到响应完成的毫秒数

#### Scenario: Latency display — 场景：延迟显示
- **WHEN** 测试完成
- **THEN** 系统应以毫秒为单位显示 TTFT 和总时间

### Requirement: Token Usage Visualization — 需求：Token 用量可视化
模型调试页面应将 Token 用量显示为可视化进度条，展示提示词 token 与补全 token 的比例，并带有数字标签。

#### Scenario: Token usage bar — 场景：Token 用量条
- **WHEN** 测试完成且返回用量数据
- **THEN** 系统应显示进度条，提示词 token（蓝色）和补全 token（绿色），并带数字标签

### Requirement: Test History — 需求：测试历史
模型调试页面应将最近 10 次测试保存在 localStorage 中。每条历史记录应包括：模型、提示词（截断至 100 字符）、系统提示词（截断至 100 字符）、温度、max_tokens、响应（截断至 500 字符）、时间戳和延迟。

#### Scenario: View test history — 场景：查看测试历史
- **WHEN** 用户点击"History"按钮
- **THEN** 系统应显示最近 10 次测试的列表，包含模型、提示词预览和时间戳

#### Scenario: Load history entry — 场景：加载历史记录
- **WHEN** 用户点击某条历史记录
- **THEN** 系统应使用保存的参数（模型、提示词、温度、max_tokens）填充表单

#### Scenario: Clear history — 场景：清除历史
- **WHEN** 用户点击"Clear History"
- **THEN** 系统应从 localStorage 中移除所有历史记录

### Requirement: Error Display Improvements — 需求：错误显示改进
模型调试页面应显示错误消息，包含：
- API 返回的错误码和消息
- 建议的操作（如 "Check API key"、"Model not available"、"Rate limited"）

#### Scenario: Provider error — 场景：提供商错误
- **WHEN** API 返回提供商特定的错误
- **THEN** 系统应显示错误消息及解决建议

#### Scenario: Network error — 场景：网络错误
- **WHEN** 发生网络错误
- **THEN** 系统应显示"Network error — check your connection"及重试按钮
