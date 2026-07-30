## 1. 引擎基础——GuardrailAction.SANITIZE

- [x] 1.1 在 `src/hecate/engine/guardrail.py` 的 `GuardrailAction` 枚举中添加 `SANITIZE = "sanitize"`
- [x] 1.2 向 `GuardrailResult` 数据类添加 `modified_data: dict | None = None` 字段
- [x] 1.3 更新模块文档字符串，移除"推迟到 P3：修改动作"注释
- [x] 1.4 编写测试：`GuardrailAction` 有 3 个成员、SANITIZE 字符串值、带 modified_data 的 `GuardrailResult`

## 2. Worker SANITIZE 处理

- [x] 2.1 更新 `LLMWorker.execute()` 处理来自 PreLLMHook 的 SANITIZE：使用 `modified_data["messages"]` 进行 LLM 调用
- [x] 2.2 更新 `LLMWorker.execute()` 处理来自 PostLLMHook 的 SANITIZE：在通道更新中使用 `modified_data["response"]`
- [x] 2.3 更新 `LLMWorker.execute_stream()` 处理来自 PreLLMHook 的 SANITIZE（与 execute 相同）
- [x] 2.4 更新 `LLMWorker.execute_stream()` 处理流式路径中来自 PostLLMHook 的 SANITIZE
- [x] 2.5 更新 `ToolWorker._execute_single_tool()` 处理来自 PostToolHook 的 SANITIZE：使用 `modified_data["result"]`
- [x] 2.6 当 SANITIZE 返回但 `modified_data=None` 时添加警告日志（视为 ALLOW）
- [x] 2.7 编写测试：来自前钩子的 LLMWorker SANITIZE、来自后钩子的 SANITIZE、ToolWorker SANITIZE、带 None 数据的 SANITIZE

## 3. AgentModel guardrail_config 列

- [x] 3.1 在 `src/hecate/models/agent.py` 的 `AgentModel` 中添加可空 `guardrail_config` JSONB 列
- [x] 3.2 向 `AgentCreateSchema` 添加 `guardrail_config` 字段，默认 None
- [x] 3.3 向 `AgentUpdateSchema` 添加 `guardrail_config` 字段，默认 None
- [x] 3.4 向 `AgentReadSchema` 添加 `guardrail_config` 字段，默认 None
- [x] 3.5 创建 Alembic 迁移：向 `agents` 表添加 `guardrail_config` JSONB 列（可空，默认 NULL）
- [x] 3.6 编写测试：带 guardrail_config 的 AgentModel CRUD、默认 None、JSON 往返

## 4. 删除 NeMo Guardrails 骨架

- [x] 4.1 删除 `src/hecate/services/security/nemo_guardrails.py`
- [x] 4.2 从 `src/hecate/services/security/middleware.py` 移除 `nemo_config` 导入
- [x] 4.3 重构 `SecurityMiddleware.check_input()` 以移除 NeMo 调用，仅使用 LLMGuardScanner
- [x] 4.4 从 `pyproject.toml` 的 `[security]` extras 中移除 `nemoguardrails`（将在 9.1a 重新添加）
- [x] 4.5 更新 `services/security/__init__.py`（如果它导出了 `nemo_config`）
- [x] 4.6 编写测试：不带 NeMo 的 SecurityMiddleware，中间件 check_input/output 仍正常工作

## 5. InputSecurityHook（特性 9.1）

- [x] 5.1 创建 `src/hecate/services/security/hooks/` 包，含 `__init__.py`
- [x] 5.2 在 `hooks/input_security.py` 中实现 `InputSecurityHook` 类（PreLLMHook）
- [x] 5.3 使用 `PIIAnonymizer` 在消息中实现 PII 匿名化，带可配置的实体类型
- [x] 5.4 使用 `LLMGuardScanner` 实现提示注入检测
- [x] 5.5 使用 `LLMGuardScanner` 实现密钥检测
- [x] 5.6 实现可配置行为：`block_on_injection` 标志（True=BLOCK，False=带警告的 SANITIZE）
- [x] 5.7 在执行上下文 `_pii_mappings` 中存储 PII 映射以供下游去匿名化
- [x] 5.8 处理 `enabled=False` 和 `guardrail_config=None`（立即返回 ALLOW）
- [x] 5.9 编写测试：干净消息、PII 检测、注入检测、密钥检测、禁用配置、实体类型过滤

## 6. OutputSecurityHook（特性 9.2）

- [x] 6.1 在 `hooks/output_security.py` 中实现 `OutputSecurityHook` 类（PostLLMHook）
- [x] 6.2 使用 `LLMGuardScanner` 实现输出毒性检测
- [x] 6.3 使用会话 `_pii_mappings` 在非流式响应中实现 PII 去匿名化
- [x] 6.4 处理 `deanonymize=False` 配置（原样传递占位符）
- [x] 6.5 处理 `enabled=False` 和 `guardrail_config=None`（立即返回 ALLOW）
- [x] 6.6 编写测试：干净响应、毒性检测、去匿名化、禁用配置、缺少映射

## 7. StreamDeanonymizer（特性 9.2 流式）

- [x] 7.1 在 `hooks/stream_deanonymizer.py` 中实现 `StreamDeanonymizer` 类
- [x] 7.2 实现 token 缓冲：检测 `[` 开始，累积直到 `]` 结束
- [x] 7.3 对完整占位符实现占位符查找和去匿名化
- [x] 7.4 对非 PII token 实现立即直通
- [x] 7.5 实现 `flush()` 方法用于流结束：去匿名化完整占位符，原样发出部分
- [x] 7.6 实现错误处理：异常时刷新缓冲区，传播错误
- [x] 7.7 编写测试：非 PII token、分割占位符、多个占位符、完全刷新、部分刷新、流式期间错误

## 8. ToolResultSecurityHook（特性 9.5）

- [x] 8.1 在 `hooks/tool_result_security.py` 中实现 `ToolResultSecurityHook` 类（PostToolHook）
- [x] 8.2 使用 `PIIAnonymizer` 在工具结果字符串中实现 PII 检测和掩码
- [x] 8.3 处理 `mask_tool_results=False` 配置（直通）
- [x] 8.4 处理未配置 `data_security`（返回 ALLOW）
- [x] 8.5 编写测试：干净结果、结果中的 PII、掩码禁用、安全禁用

## 9. 数据安全——存储和加密（特性 9.5）

- [x] 9.1 在 `src/hecate/models/pii_mapping.py` 中创建 `PIIMappingModel` ORM 模型，字段包括：id、session_id、placeholder、encrypted_value、pii_type、created_at
- [x] 9.2 在 (session_id, placeholder) 上添加唯一约束
- [x] 9.3 通过 models `__init__.py` 中的导入将 `PIIMappingModel` 添加到 `Base.metadata`
- [x] 9.4 创建 Alembic 迁移：创建 `pii_mappings` 表
- [x] 9.5 在 `services/security/encryption.py` 中实现 Fernet 加密/解密辅助函数
- [x] 9.6 实现 `mask_and_encrypt` 模式：加密原始 PII，存储在 `PIIMappingModel` 中
- [x] 9.7 当请求 `mask_and_encrypt` 但没有 `FERNET_KEY` 时实现 `ConfigurationError`
- [x] 9.8 实现 `mask_only` 模式：将 PII 替换为不可逆占位符，不存储
- [x] 9.9 编写测试：PIIMappingModel CRUD、Fernet 加密/解密、两种存储模式、缺少 Fernet 密钥

## 10. 安全钩子工厂

- [x] 10.1 实现 `create_security_hooks(guardrail_config: dict | None) -> SecurityHookSet` 工厂函数
- [x] 10.2 定义 `SecurityHookSet` 命名元组：`(pre_llm_hook, post_llm_hook, pre_tool_hook, post_tool_hook)`
- [x] 10.3 当配置为 None 或所有部分禁用时，工厂返回 NoOp 钩子
- [x] 10.4 使用 `input_security` 配置部分构造 InputSecurityHook
- [x] 10.5 使用 `output_security` 配置部分构造 OutputSecurityHook
- [x] 10.6 使用 `data_security` 配置部分构造 ToolResultSecurityHook
- [x] 10.7 从 `hooks/__init__.py` 导出 `create_security_hooks` 和 `SecurityHookSet`
- [x] 10.8 编写测试：带 None 配置的工厂、带禁用部分的工厂、带完整配置的工厂

## 11. LLMGuardScanner 增强

- [x] 11.1 向 `ScanResult` 数据类添加 `sanitized_text: str | None = None` 字段
- [x] 11.2 更新 `scan_prompt()` 以捕获并返回来自 Anonymize 扫描器的清洗后文本
- [x] 11.3 更新 `scan_output()` 以捕获并返回来自输出扫描器的清洗后文本
- [x] 11.4 更新 mock 扫描器以返回 sanitized_text
- [x] 11.5 编写测试：带 sanitized_text 的 ScanResult、scan_prompt 返回匿名化文本

## 12. PII 审计事件

- [x] 12.1 在 `engine/eventstore.py` 的 `EventType` 枚举中添加 `PII_DETECTED` 事件类型
- [x] 12.2 在 `audit_pii_events` 为 True 时，在 InputSecurityHook 中实现审计日志记录
- [x] 12.3 在 `audit_pii_events` 为 True 时，在 OutputSecurityHook 中实现审计日志记录
- [x] 12.4 在 `audit_pii_events` 为 True 时，在 ToolResultSecurityHook 中实现审计日志记录
- [x] 12.5 确保审计事件包含 pii_type 和占位符计数，但不包含原始 PII 值
- [x] 12.6 编写测试：审计事件已发出、审计事件不包含原始 PII、审计禁用

## 13. 功能目录和路线图更新

- [x] 13.1 更新 `docs/features/feature-catalog.md`：验证后将特性 9.1、9.2 标记为 ✅
- [x] 13.2 更新 `docs/features/feature-catalog.md`：将 `GuardrailAction.modify`（SANITIZE）从 P3 移至 P2 状态
- [x] 13.3 更新 `docs/features/roadmap.md`：将安全里程碑项标记为完成
- [x] 13.4 更新功能目录中的统计计数
