## Why — 原因

`services/security/` 中的安全基础设施已构建完整（用于提示注入/PII/密钥/毒性检测的 LLMGuardScanner、用于可逆掩码的 PIIAnonymizer、用于编排的 SecurityMiddleware），但完全处于休眠状态——没有组件导入 SecurityMiddleware、LLMWorker 和 ToolWorker 默认使用 NoOp 钩子、GuardrailAction 仅支持 ALLOW/BLOCK 而没有在传输中转换数据的机制。因此，平台具有零运行时安全性：没有提示注入检测、没有 PII 编辑、没有输出毒性过滤、没有工具结果清洗。特性 9.1（输入安全）、9.2（输出安全）和 9.5（数据安全）都依赖于将现有基础设施接入引擎的防护栏钩子生命周期，并具备按代理可配置性。

## What Changes — 变更内容

- 向引擎防护栏 ABC 添加 `GuardrailAction.SANITIZE` 和 `GuardrailResult.modified_data: dict | None`，使钩子能够转换数据（PII 掩码、内容重写），而不仅仅是允许/阻止
- 更新 LLMWorker 和 ToolWorker 以处理 SANITIZE 动作：当返回时用 `modified_data` 替换消息/响应/工具结果
- 向 `AgentModel` 添加 `guardrail_config` JSONB 列（含 Alembic 迁移），启用按代理安全配置（启用哪些扫描器、PII 实体类型、存储模式等）
- 实现 `InputSecurityHook`（PreLLMHook）：通过 LLMGuardScanner 进行提示注入检测，通过 PIIAnonymizer 进行 PII 匿名化，当检测到 PII 时返回带掩码消息的 SANITIZE，或当检测到注入时返回 BLOCK
- 实现 `OutputSecurityHook`（PostLLMHook）：通过 LLMGuardScanner 进行输出毒性检测，对非流式响应进行 PII 去匿名化，当发现问题时返回带清洗后响应的 SANITIZE
- 实现 `StreamDeanonymizer`：基于缓冲区的 token 累加器，用于流式 LLM 响应，收集部分 token，去匿名化完整的 PII 占位符，仅发出完全还原的文本——确保流式传输永远不会向最终用户暴露原始 PII 占位符
- 实现 `ToolResultSecurityHook`（PostToolHook）：在工具执行结果中进行 PII 检测和掩码，当发现 PII 时返回带掩码结果的 SANITIZE
- 添加按代理可配置的 PII 存储模式：`mask_only`（默认，存储前不可逆掩码）或 `mask_and_encrypt`（用于可逆查找的 Fernet 加密 PIIMappingModel）
- 删除 `nemo_guardrails.py` 骨架（仅正则表达式的玩具代码，没有真正的 NeMo 运行时；特性 9.1a 将从头重新设计）
- 重构 `SecurityMiddleware` 以移除 NeMo 依赖，作为新钩子的薄外观以保持向后兼容

## Capabilities — 能力

### New Capabilities — 新增能力
- `input-security`：实现 PreLLMHook 的输入安全钩子——提示注入检测、PII 匿名化、特性 9.1 的有害内容过滤
- `output-security`：实现 PostLLMHook 的输出安全钩子——毒性检测、PII 去匿名化（非流式 + 用于流式的 StreamDeanonymizer）、特性 9.2 的敏感输出阻止
- `data-security`：静态数据安全——数据库存储前的 PII 掩码、可配置的存储模式（mask_only/mask_and_encrypt）、用于可逆查找的 Fernet 加密 PIIMappingModel、用于工具结果清洗的 PostToolHook、特性 9.5 的审计事件日志

### Modified Capabilities — 修改的能力
- `guardrail-hook`：添加 `GuardrailAction.SANITIZE` 枚举成员、在 `GuardrailResult` 中添加 `modified_data: dict | None`、更新 NoOp 实现以支持新字段、更新三成员枚举和新数据类字段的规范场景
- `security-mcp`：从 SecurityMiddleware 移除 NeMo Guardrails 骨架集成、更新扫描器编排以使用新的基于钩子的架构、将 PIIAnonymizer 与 LLMGuardScanner Anonymize 扫描器集成以进行协调检测

## Impact — 影响

- **Engine layer**（`engine/guardrail.py`）：GuardrailAction 枚举增加 SANITIZE 成员；GuardrailResult 数据类增加 modified_data 字段——所有现有 NoOp 实现保持兼容
- **Workers**（`engine/workers/llm_worker.py`、`engine/workers/tool_worker.py`）：两者在 execute() 和 execute_stream() 路径中都增加了 SANITIZE 动作处理；LLMWorker 流式路径增加了 StreamDeanonymizer 集成
- **Models**（`models/agent.py`）：AgentModel 增加 `guardrail_config` JSONB 列；AgentCreateSchema、AgentUpdateSchema、AgentReadSchema 增加相应字段（使用 Pydantic 别名模式）
- **Database**：添加 `guardrail_config` 列到 `agents` 表的新 Alembic 迁移
- **Services**（`services/security/`）：删除 `nemo_guardrails.py`；重构 `middleware.py` 以移除 NeMo 导入；添加包含 InputSecurityHook、OutputSecurityHook、ToolResultSecurityHook、StreamDeanonymizer 的 `hooks/` 子模块
- **Config**（`core/config.py`）：`FERNET_KEY` 设置（已声明）将积极用于 mask_and_encrypt 模式
- **Tests**：每个钩子实现的新测试文件、StreamDeanonymizer 和更新的防护栏 ABC 测试；现有的 guardrail-hook 和 security-mcp 测试需要为 SANITIZE 动作更新
- **Dependencies**：无新依赖——使用现有的 llm-guard、cryptography（Fernet）和 regex 库
