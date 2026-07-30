## 新增需求

### 需求：Provider 策略为目标 LLM 适配上下文
系统须在将组装后的上下文传递给 LLM 服务之前，应用 provider 特定的策略，根据目标 provider 的偏好适配消息格式、工具定义和 system prompt 结构。

#### 场景：OpenAI provider 策略
- **当** 目标模型以 "gpt-" 开头且上下文中包含超过 2000 token 的 system 消息
- **则** OpenAI 策略须将 system 消息截断至 2000 token 并附加截断说明

#### 场景：Anthropic provider 策略
- **当** 目标模型以 "claude-" 开头且上下文中包含工具定义
- **则** Anthropic 策略须使用 Anthropic 原生工具格式（input_schema 替代 parameters）格式化工具定义

#### 场景：未知 provider 使用默认策略
- **当** 目标模型不匹配任何已知 provider 前缀
- **则** 默认策略须原样传递上下文，不做修改

### 需求：按模型名称选择策略
系统须根据模型标识符自动选择合适的 provider 策略。

#### 场景：模型名称映射
- **当** 模型为 "gpt-4o"
- **则** 系统须选择 `OpenAIStrategy`

#### 场景：Claude 的模型名称映射
- **当** 模型为 "claude-3-5-sonnet-20241022"
- **则** 系统须选择 `AnthropicStrategy`

#### 场景：回退到默认
- **当** 模型为 "qwen-plus" 且未注册 Qwen 专属策略
- **则** 系统须选择 `DefaultStrategy`

### 需求：Provider 特定的 system 消息处理
系统须根据 provider 要求适配 system 消息的构建方式。

#### 场景：Anthropic system 消息作为顶级参数
- **当** 目标 provider 为 Anthropic
- **则** 策略须从 messages 数组中提取 system 消息，并确保作为顶级 `system` 参数传递（Anthropic API 约定）

#### 场景：OpenAI system 消息保留在 messages 数组中
- **当** 目标 provider 为 OpenAI
- **则** 策略须将 system 消息作为 messages 数组的第一个元素保留

### 需求：Provider 策略可扩展
系统须允许在不修改现有代码的情况下注册自定义 provider 策略。

#### 场景：注册自定义策略
- **当** 用户为模型前缀 "deepseek-" 注册自定义策略
- **则** 后续使用以 "deepseek-" 开头的模型的调用须使用自定义策略而非默认策略
