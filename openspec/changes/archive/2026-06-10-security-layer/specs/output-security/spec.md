## ADDED Requirements — 新增需求

### 需求：OutputSecurityHook 实现 PostLLMHook
`OutputSecurityHook` 应实现 `PostLLMHook` ABC，为 LLM 响应提供输出毒性检测和 PII 去匿名化

#### 场景：干净响应通过
- **当** 调用 `on_post_llm_call(response, messages)` 且响应不含毒性和 PII 占位符
- **则** 应返回 `GuardrailResult(action=GuardrailAction.ALLOW)`

#### 场景：检测到响应中的毒性
- **当** LLMGuardScanner Toxicity 扫描器检测到风险分数高于 `output_security.toxicity_threshold`
- **则** 应返回 `GuardrailResult(action=GuardrailAction.BLOCK, reason="Toxic output detected: ...")`

#### 场景：非流式响应中的 PII 占位符去匿名化
- **当** 响应包含 PII 占位符（例如 `[EMAIL_1]`）且 `output_security.deanonymize` 为 True
- **则** 应从会话 PII 映射中用原始值替换占位符，并返回 `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"response": <deanonymized_response>})`

#### 场景：去匿名化已禁用
- **当** `output_security.deanonymize` 为 False
- **则** PII 占位符应原样传递给用户而不替换

#### 场景：代理安全已禁用
- **当** `output_security.enabled` 为 False 或 guardrail_config 为 None
- **则** 应返回 `GuardrailResult(action=GuardrailAction.ALLOW)` 而不扫描

### 需求：StreamDeanonymizer 处理流式 PII
`StreamDeanonymizer` 应缓冲流式 token，在发出给用户之前检测并去匿名化完整的 PII 占位符

#### 场景：非 PII token 立即发出
- **当** 传入 token 不以 `[` 开头且缓冲区为空
- **则** token 应立即发出而不缓冲

#### 场景：PII 占位符跨 token 分割
- **当** token `["Contact [", "EMAIL_", "1] for help"]` 顺序到达
- **则** StreamDeanonymizer 应缓冲直到 `[EMAIL_1]` 完整，去匿名化为原始值，并发出 `"Contact john@example.com for help"`

#### 场景：流以部分占位符结束
- **当** 流以缓冲的部分占位符结束（例如 `"[EMA"`）
- **则** 部分缓冲区应按原样刷新（无法对不完整的占位符进行去匿名化）

#### 场景：流以完整占位符结束
- **当** 流以完全缓冲的占位符结束（例如 `"[EMAIL_1]"`）
- **则** 应去匿名化并发出原始值

#### 场景：流中有多个 PII 占位符
- **当** 流包含 `"[EMAIL_1] and [PHONE_1]"`
- **则** 每个完整占位符应在完成时逐个去匿名化

### 需求：StreamDeanonymizer 在错误时刷新
当流由于错误终止时，`StreamDeanonymizer` 应刷新任何缓冲内容

#### 场景：流式期间出错
- **当** 流式期间发生异常且存在缓冲内容
- **则** 缓冲区应按原样刷新，错误应传播
