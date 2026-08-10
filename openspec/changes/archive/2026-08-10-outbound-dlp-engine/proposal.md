## Why

Hecate 当前的数据安全防护集中在**输入侧**（输入 anonymize）和**有限输出侧**（deanonymize + 硬硬规则的 PII regex），但缺乏统一的 **出站 DLP 引擎**：没有策略模型，没有 per-entity 配置，没有 MCP 响应扫描，没有流式增量扫描。Agent DLP 是企业上线 P0 必备——业界传统 DLP（Microsoft Purview、Symantec、Forcepoint、Google Cloud DLP）20 年标准实践，加上 MCP 响应扫描的差异化能力（业界无人做 PII/secrets DLP），填补 Hecate 企业合规闭环。

## What Changes

- 新增统一 DLP 引擎（DLPScanner + Recognizer Registry + Policy Resolver），支持配置化的 per-entity per-direction 策略
- 新增 4 个 Recognizer 实现：RegexRecognizer（替代现有 PIIAnonymizer）、SecretsRecognizer（detect-secrets 包装）、PresidioRecognizer（可选 NER）、DictionaryRecognizer（自定义词典）
- 新增 3 个数据模型：DLPPolicyModel、DLPCustomRegexModel、DLPDictionaryModel，支持 org→workspace→agent 三级 override + `is_locked` 硬约束
- 新增 DLP REST API（policy CRUD + custom regex + dictionary + test dry-run）
- 新增 EgressFilter ABC + DLPEgressFilter，在 HecateMCPClient.call_tool() 返回前拦截 MCP 响应
- 改造现有 4 个 GuardrailHook：InputSecurityHook、OutputSecurityHook、ToolResultSecurityHook 接入 DLPScanner；新增 PreToolHook DLP 扫描 tool 参数
- 新增 StreamingDLPWrapper，支持流式输出增量扫描（300 char buffer + 10 overlap）+ 最终全量扫描兜底
- 新增内置默认规则（secrets→BLOCK locked，PII→MASK，EMAIL→AUDIT 灰度）
- 新增 [security] extra 依赖：presidio-analyzer、presidio-anonymizer、detect-secrets、spacy

## Capabilities

### New Capabilities
- `dlp-scanner`: DLP 检测引擎（Recognizer Registry + DLPScanner + Policy Resolver），检测逻辑分层（Detection→Policy→Enforcement）
- `dlp-policy-management`: DLP 策略 DB 模型 + REST API + 三级 override + `is_locked` 硬约束
- `dlp-recognizers`: 4 个 Recognizer 实现（Regex、Secrets、Presidio、Dictionary）
- `dlp-egress-filter`: EgressFilter ABC + DLPEgressFilter，在 MCP/A2A/Webhook 响应出口统一拦截
- `dlp-streaming`: StreamingDLPWrapper，流式输出增量扫描

### Modified Capabilities
- `input-security`: InputSecurityHook 改造——secrets 检测改用 DLPScanner（替代 LLMGuardScanner Secrets），PII 检测保留 InputSecurityHook 内部的 anonymize（边界 1 机制），可选路径走 DLPScanner
- `output-security`: OutputSecurityHook 改造——deanonymize 后新增 DLPScanner scan（边界 2：egress policy），毒性检测保留
- `data-security`: ToolResultSecurityHook 改造——PII 检测改用 DLPScanner（替代硬编码 PIIAnonymizer.PATTERNS），保留 PII storage mode 配置
- `mcp-client-real`: HecateMCPClient 改造——构造器新增 `egress_filters` 参数，call_tool() 在 MCP 响应进入 Context前经过 EgressFilter chain
- `security-findings`: SecurityFindingModel 不变——DLP finding 复用（rule_name 前缀 `dlp:`），REST API 新增 feedback endpoint

## Impact

- **新增文件**：`models/dlp.py`、`services/security/dlp/*`（13 文件，含 recognizers 子包）、`services/security/egress.py`、`api/management/dlp.py`、Alembic 迁移
- **修改文件**：`services/mcp/client.py`、3 个 GuardrailHook、`core/config.py`、`main.py`、`pyproject.toml`
- **依赖**：`[security]` extra 新增 presidio-analyzer、presidio-anonymizer、detect-secrets、spacy（含 en_core_web_lg 模型）
- **数据迁移**：3 张新表（dlp_policies、dlp_custom_regex、dlp_dictionaries），部署时自动创建内置默认规则
- **测试**：~40-50 个新测试，覆盖 Recognizer、Scanner、Policy、EgressFilter、Streaming、API、MCP 集成
- **破坏性变更**：PIIAnonymizer.PATTERNS 不再被 ToolResultSecurityHook 直接引用（改用 RegexRecognizer 同一套 pattern），现有调用方不受影响（class 保留，PATTERNS 迁移到 RegexRecognizer）