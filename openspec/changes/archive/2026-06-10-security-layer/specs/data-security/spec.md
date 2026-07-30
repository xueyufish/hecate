## ADDED Requirements — 新增需求

### 需求：ToolResultSecurityHook 实现 PostToolHook
`ToolResultSecurityHook` 应实现 `PostToolHook` ABC，在工具执行结果存储到通道或返回到 LLM 之前检测和掩码 PII

#### 场景：干净工具结果通过
- **当** 使用不含 PII 的结果调用 `on_post_tool_call(name, result, context)`
- **则** 应返回 `GuardrailResult(action=GuardrailAction.ALLOW)`

#### 场景：工具结果中检测到 PII
- **当** 工具结果字符串包含 PII 模式且 `data_security.mask_tool_results` 为 True
- **则** 应在结果中匿名化 PII 并返回 `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"result": <masked_result>})`

#### 场景：工具结果掩码已禁用
- **当** `data_security.mask_tool_results` 为 False
- **则** 工具结果应原样通过而不进行 PII 掩码

#### 场景：代理安全已禁用
- **当** 未配置 `data_security` 或 `guardrail_config` 为 None
- **则** 应返回 `GuardrailResult(action=GuardrailAction.ALLOW)` 而不扫描

### 需求：PII 存储模式配置
系统应支持两种由 `guardrail_config.data_security.pii_storage_mode` 控制的 PII 存储模式

#### 场景：mask_only 模式（默认）
- **当** `pii_storage_mode` 为 `"mask_only"` 或未指定
- **则** PII 应在数据库存储前替换为不可逆占位符
- **则** 不应持久化任何原始 PII 值

#### 场景：mask_and_encrypt 模式
- **当** `pii_storage_mode` 为 `"mask_and_encrypt"`
- **则** 原始 PII 值应使用 Fernet 加密并存储在 `PIIMappingModel` 表中
- **则** 每个映射应以 (session_id, placeholder) 为键
- **则** 加密值应由授权组件使用 Fernet 密钥恢复

#### 场景：Fernet 密钥未配置
- **当** `pii_storage_mode` 为 `"mask_and_encrypt"` 且未设置 `FERNET_KEY`
- **则** 系统应在钩子构造时抛出 `ConfigurationError`

### 需求：用于加密映射的 PIIMappingModel
系统应定义 `PIIMappingModel` ORM 模型，用于在 `mask_and_encrypt` 模式下存储 Fernet 加密的 PII 映射

#### 场景：模型字段
- **当** 定义 `PIIMappingModel`
- **则** 应包含字段：`id`（UUID PK）、`session_id`（UUID，指向 sessions 的 FK）、`placeholder`（str，例如 "[EMAIL_1]"）、`encrypted_value`（bytes，Fernet 加密）、`pii_type`（str，例如 "email"）、`created_at`（datetime）

#### 场景：唯一约束
- **当** 保存映射
- **则** (session_id, placeholder) 的组合应是唯一的

### 需求：PII 审计事件记录
当 `data_security.audit_pii_events` 为 True 时，系统应将 PII 检测事件记录到 EventStore

#### 场景：检测到 PII 并记录
- **当** 在任何数据流（输入、输出、工具结果）中检测到 PII 且审计已启用
- **则** 应将事件追加到 EventStore，类型为 `PII_DETECTED`，包含 pii_type 和占位符计数，但不包含原始 PII 值

#### 场景：审计已禁用
- **当** `audit_pii_events` 为 False
- **则** 不应向 EventStore 记录 PII 检测事件

### 需求：AgentModel guardrail_config 列
`AgentModel` 应具有 `guardrail_config` JSONB 列，用于按代理安全配置

#### 场景：列添加到 AgentModel
- **当** 运行迁移
- **则** `agents` 表应具有可空 `guardrail_config` JSONB 列，默认 `NULL`

#### 场景：使用防护栏配置创建代理
- **当** 在请求体中带有 `guardrail_config` 创建代理
- **则** 配置应存储在 JSONB 列中

#### 场景：无防护栏配置创建代理
- **当** 无 `guardrail_config` 创建代理
- **则** 列应为 `NULL`，表示此代理的安全钩子已禁用

#### 场景：防护栏配置已更新
- **当** 使用新的 `guardrail_config` 更新代理
- **则** 存储的配置应以原子方式替换
