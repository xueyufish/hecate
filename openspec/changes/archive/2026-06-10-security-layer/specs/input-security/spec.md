## ADDED Requirements — 新增需求

### 需求：InputSecurityHook 实现 PreLLMHook
`InputSecurityHook` 应实现 `PreLLMHook` ABC，在用户消息到达 LLM 之前提供提示注入检测、PII 匿名化和有害内容过滤

#### 场景：干净消息通过
- **当** 使用不包含 PII 或注入模式的消息调用 `on_pre_llm_call(messages, model, tools)`
- **则** 应返回 `GuardrailResult(action=GuardrailAction.ALLOW)`

#### 场景：消息中检测到 PII
- **当** 消息包含 PII（email、phone、SSN、credit card、IP address）且 `input_security.pii_entities` 包含检测到的类型
- **则** 应在消息中匿名化 PII 并返回 `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"messages": <anonymized_messages>})`

#### 场景：检测到提示注入
- **当** LLMGuardScanner PromptInjection 扫描器检测到风险分数高于配置的阈值
- **则** 当 `input_security.block_on_injection` 为 True 时，应返回 `GuardrailResult(action=GuardrailAction.BLOCK, reason="Prompt injection detected: ...")`
- **则** 当 `input_security.block_on_injection` 为 False 时，应返回 `GuardrailResult(action=GuardrailAction.SANITIZE, reason="Prompt injection warning", modified_data={"messages": <messages_with_warning>})`

#### 场景：消息中检测到密钥
- **当** LLMGuardScanner Secrets 扫描器检测到 API 密钥、token 或凭据
- **则** 应返回 `GuardrailResult(action=GuardrailAction.BLOCK, reason="Secrets detected in input")`

#### 场景：代理安全已禁用
- **当** `input_security.enabled` 为 False 或 guardrail_config 为 None
- **则** 应返回 `GuardrailResult(action=GuardrailAction.ALLOW)` 而不扫描

### 需求：InputSecurityHook 保留 PII 映射
`InputSecurityHook` 应维护匿名化 PII 占位符到原始值的会话级映射，使下游 OutputSecurityHook 能够进行去匿名化

#### 场景：为会话存储映射
- **当** 在会话的消息中匿名化 PII
- **则** 占位符到原始值的映射应在执行上下文中以 `_pii_mappings` 键存储，供 OutputSecurityHook 使用

#### 场景：同一类型的多个 PII 实例
- **当** 在消息中找到多个电子邮件地址
- **则** 每个应接收唯一的占位符（`[EMAIL_1]`、`[EMAIL_2]` 等），带有单独的映射

### 需求：InputSecurityHook 可配置实体类型
`InputSecurityHook` 应接受可配置的 PII 实体类型列表，由 `guardrail_config.input_security.pii_entities` 控制

#### 场景：自定义实体列表
- **当** `pii_entities` 设置为 `["email", "phone"]`
- **则** 仅 email 和 phone PII 应被匿名化；SSN、credit card 和 IP address 应原样通过

#### 场景：默认实体列表
- **当** 未指定 `pii_entities`
- **则** 所有支持的实体类型（email、phone、credit_card、ssn、ip_address）都应被检测
