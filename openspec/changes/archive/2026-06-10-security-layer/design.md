## Context — 上下文

Hecate 在 `services/security/` 中有一个完全构建好的安全服务层，包含三个组件：

1. **LLMGuardScanner**（`llm_guard.py`）——延迟加载 llm-guard 扫描器并带 mock 回退。提示扫描器：PromptInjection（阈值=0.5）、Anonymize、Secrets。输出扫描器：Toxicity（阈值=0.7）。返回 `ScanResult(is_safe, score, issues)`

2. **PIIAnonymizer**（`anonymizer.py`）——基于正则表达式的可逆 PII 掩码，用于 email、phone、credit_card、ssn、ip_address。返回带占位符模式 `[TYPE_N]` 的 `AnonymizedText(text, mappings)`

3. **SecurityMiddleware**（`middleware.py`）——编排 LLMGuardScanner + NeMo Guardrails。当前唯一的集成点，但**没有人导入它**

引擎层在 `engine/guardrail.py` 中有四个防护栏钩子 ABC（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook），带 NoOp 默认值。LLMWorker 和 ToolWorker 在构造时接受钩子并正确处理 BLOCK，但默认使用 NoOp

**差距**：服务层安全扫描器和引擎层钩子之间没有桥梁。GuardrailAction 只有 ALLOW/BLOCK——没有传输中数据转换机制（PII 掩码）。AgentModel 没有按代理安全配置。NeMo Guardrails 骨架是仅正则表达式的玩具代码

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将现有的安全扫描器接入引擎防护栏钩子，使每个 LLM 调用、工具执行和流式响应都通过安全检查
- 通过 `guardrail_config` JSONB 列启用按代理安全配置
- 支持用于传输中 PII 掩码的 SANITIZE 动作，带可配置的存储模式
- 通过基于缓冲区的去匿名化安全处理流式 PII（不向用户泄漏原始占位符）
- 覆盖所有四个数据流层：用户输入、LLM 输出、工具结果、数据库存储

**非目标：**
- NeMo Guardrails 集成——删除骨架，推迟到 9.1a
- 提示注入模型微调——按原样使用 LLM Guard 的 DeBERTa-v3
- 全字段内容加密——破坏搜索能力；改用 PII 级掩码
- 内容审查（关键词过滤）——推迟到 9.2a（P3）
- 幻觉检测——推迟到未来工作
- 多租户安全隔离——已在特性 10.5 中完成

## Decisions — 决策

### D1：向 GuardrailAction 枚举添加 SANITIZE 动作

**决策**：将 `GuardrailAction` 从 `{ALLOW, BLOCK}` 扩展为 `{ALLOW, BLOCK, SANITIZE}`。向 `GuardrailResult` 添加 `modified_data: dict | None = None`

**理由**：PII 掩码需要钩子返回转换后的数据（匿名化消息、掩码响应）。ALLOW 表示"原样通过"；BLOCK 表示"停止执行"；SANITIZE 表示"用修改后的数据继续"。这是 AWS Bedrock Guardrails 和 Google Cloud DLP 使用的标准三动作模式

**考虑的替代方案**：返回带有修改数据的 ALLOW——混淆了两个关注点，不清楚数据是否被转换

### D2：通过 JSONB 列进行按代理防护栏配置

**决策**：向 `AgentModel` 添加 `guardrail_config` JSONB 列。结构：

```python
{
    "input_security": {
        "enabled": True,
        "prompt_injection_threshold": 0.5,
        "pii_entities": ["email", "phone", "ssn", "credit_card", "ip_address"],
        "block_on_injection": True
    },
    "output_security": {
        "enabled": True,
        "toxicity_threshold": 0.7,
        "deanonymize": True
    },
    "data_security": {
        "pii_storage_mode": "mask_only",  # 或 "mask_and_encrypt"
        "mask_tool_results": True,
        "audit_pii_events": True
    }
}
```

**理由**：不同的代理处理不同的敏感度级别。客户支持代理需要积极的 PII 掩码；内部开发工具可能只需要提示注入检测。JSONB 避免了配置变更的模式迁移

**考虑的替代方案**：仅全局配置——已拒绝，因为企业部署需要按代理控制。独立的安全配置文件表——对 P2 范围来说是过度设计

### D3：通过基于缓冲区的 StreamDeanonymizer 实现流式 PII

**决策**：实现 `StreamDeanonymizer`，缓冲传入 token，等待完整的 PII 占位符（`[EMAIL_1]`），然后去匿名化并发出。部分缓冲区保持直到更多 token 到达或流结束

```
Token stream: "Contact [", "EMAIL_", "1]", " for details"
              ↓ buffer  ↓ buffer ↓ flush  ↓ pass-through
Emitted:      ""        ""       "john@x.com" " for details"
```

**理由**：LLM token 可能将 PII 占位符分割到多个块中。向用户发出原始 `[EMAIL_1]` 是 PII 泄露（揭示发生了掩码和占位符格式）。基于缓冲区的方法被 Upsonic 的 StreamDeanonymizer 和 Salesforce Einstein Trust Layer 使用

**考虑的替代方案**：流后去匿名化（缓冲整个响应）——违背了流式传输的目的。每个 token 正则表达式替换——对于分割的占位符不可靠

### D4：可配置的 PII 存储模式

**决策**：两种模式由 `guardrail_config.data_security.pii_storage_mode` 控制：

- **`mask_only`**（默认）：PII 在存储前替换为不可逆占位符。无法恢复。匹配 Salesforce Einstein Trust Layer 模式
- **`mask_and_encrypt`**：原始 PII 值使用 Fernet 加密并存储在单独的 `PIIMappingModel` 表中，以 (session_id, placeholder) 为键。允许授权恢复用于合规/审计。匹配 Google DLP 可逆标记化模式

**理由**：企业客户有不同的合规要求。有些需要不可逆掩码（GDPR 被遗忘权简化）；其他需要为授权用例（客户支持升级）恢复原始值的能力

**考虑的替代方案**：仅 `mask_only`——对企业来说限制太大。仅 `mask_and_encrypt`——对大多数部署来说不必要的复杂性和密钥管理负担

### D5：删除 NeMo Guardrails 骨架

**决策**：完全删除 `services/security/nemo_guardrails.py`。从 `middleware.py` 移除其导入。特性 9.1a 将从零开始设计真正的 NeMo 集成

**理由**：当前的 `NeMoGuardrailsConfig` 是仅正则表达式的骨架，包含硬编码模式（"hack"、"exploit"、"bomb"）。它提供零真实安全性，并造成虚假的安全感。真正的 NeMo Guardrails 库需要 Colang 运行时、配置文件和异步服务器——完全不同的架构

### D6：通过工厂函数进行钩子接入

**决策**：创建 `create_security_hooks(guardrail_config: dict) -> SecurityHookSet` 工厂函数，返回根据代理 guardrail_config 配置的 `(pre_llm_hook, post_llm_hook, pre_tool_hook, post_tool_hook)` 命名元组。Worker 在构造时接收这些钩子（现有模式）

**理由**：Worker 已通过构造函数注入接受钩子。工厂将"读取配置 → 创建钩子"的逻辑集中在一个地方，保持 worker 不知道安全实现细节

**考虑的替代方案**：Worker 直接实例化钩子——违反关注点分离，在 engine/ 和 services/security/ 之间创建导入循环

### D7：仅对 mask_and_encrypt 模式使用 Fernet

**决策**：使用 `cryptography.fernet.Fernet`（已通过 `FERNET_KEY` 配置可选依赖）专门用于加密 `mask_and_encrypt` 模式中的 PII 映射。不加密完整的内容字段

**理由**：全字段加密破坏搜索/查询能力（Google DLP、AWS Macie、Salesforce 的行业共识）。Fernet 提供适用于可逆标记化的对称认证加密。密钥已在 `core/config.py` 中声明

## Risks / Trade-offs — 风险 / 权衡

**[流式延迟]** 基于缓冲区的去匿名化器增加了与占位符长度成比例的延迟（约 10-20 字符）。→ 缓解措施：立即发出非 PII 文本；仅在检测到 `[` 时缓冲

**[崩溃时的 PII 映射丢失]** 进程崩溃时内存中的 `AnonymizedText.mappings` 丢失，破坏进行中流的去匿名化。→ 缓解措施：`mask_and_encrypt` 模式将映射持久化到数据库；`mask_only` 模式没有任何映射可丢失。对于流式，StreamDeanonymizer 在流结束或错误时刷新

**[正则表达式 PII 误报/漏报]** PIIAnonymizer 使用正则表达式模式，会遗漏上下文相关的 PII（姓名、地址）并可能对格式化数字产生误报。→ 缓解措施：LLM Guard 的 Anonymize 扫描器使用 Presidio + BERT NER 实现更准确的检测。两者分层：PIIAnonymizer 用于快速正则表达式，LLM Guard Anonymize 用于 NER

**[迁移风险]** 添加 `guardrail_config` 列需要 Alembic 迁移。现有代理获得 `NULL` → 视为"安全已禁用"（向后兼容）。→ 缓解措施：`None` guardrail_config 表示不创建钩子（工厂返回 NoOp 集）

**[性能]** 在每个 LLM 调用上运行 3 个输入扫描器（PromptInjection、Anonymize、Secrets）增加延迟。→ 缓解措施：扫描器是异步兼容的；Anonymize 扫描器可通过配置为可选的；延迟加载意味着如果禁用则无开销

**[Fernet 密钥管理]** `mask_and_encrypt` 模式需要安全的 Fernet 密钥存储。密钥轮换尚不支持。→ 缓解措施：记录密钥管理要求；将密钥轮换推迟到 P3
