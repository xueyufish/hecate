## Context

Hecate 当前有 `PIIAnonymizer`（5 个硬编码 regex，全局单例）和 `LLMGuardScanner`（包装 llm_guard 库）两套独立的检测体系。它们被 InputSecurityHook、OutputSecurityHook、ToolResultSecurityHook 直接调用，没有策略抽象、没有配置化、没有 per-entity action、没有 MCP 响应扫描。MCP 响应直接进入 Agent Context，无任何出站过滤。Deanonymizer（OutputSecurityHook）做的是信任边界 1 的还原，不做策略执行；现有架构缺一道信任边界 2 的 egress gate。

业界 15+ 平台调研发现：Bedrock Guardrails、Microsoft Presidio、llm-guard、guardrails-ai、Dify、Salesforce Einstein Trust Layer 均无 MCP 响应 DLP 扫描。阿里 AgentScope V2 扫描 MCP tool 输出但仅做内容审核（非 PII/secrets DLP）。Microsoft Purview、Symantec、Forcepoint、Google Cloud DLP 20 年传统 DLP 实践均采用三层架构（Detection→Policy→Enforcement），提供 AUDIT action、层级 override、用户反馈机制——这 4 项已成为业界标准（≥3 家独立验证）。

## Goals / Non-Goals

**Goals:**
- 统一现有 PIIAnonymizer 和 LLMGuardScanner 的检测逻辑为 DLPScanner（三层架构）
- 在 4 个不可信边界点接入 DLP 扫描（PreLLM、PostLLM、PreTool、PostTool）
- 新增 MCP 响应 EgressFilter 拦截（EgressFilter ABC 通用模式）
- 新增流式输出增量扫描（StreamingDLPWrapper，300/10 buffer/overlap）
- DLPPolicyModel DB 模型 + 三级 override（agent→workspace→org）+ `is_locked` 硬约束
- REST API 管理策略（CRUD + test dry-run）
- 复用 SecurityFindingModel（rule_name 前缀 `dlp:`）+ 反馈机制
- 业界对齐：fail-open 默认、PerForcepoint 三阶渐进上线、Symantec 风格 incident workflow

**Non-Goals:**
- 非文本内容扫描（v1 图片 OCR、文件二进制扫描）
- LLM-as-judge 语义检测（v2 考虑，参考 Presidio LangExtract）
- CASB / Network DLP / Endpoint DLP（Hecate 是 Agent 平台，不覆盖网络层/终端层）
- 与传统 DLP 产品（如 Purview）的联盟集成
- DLP 策略的版本控制与时间机器（v1 直接覆盖即可）

## Decisions

### D1: 三层分离架构（Detection → Policy → Enforcement）

**选择**：DLPRecognizer（检测）→ DLPPolicyResolver（策略）→ DLPOperator-style execution（执行）

**替代方案**：
- 单一类（像 llm-guard scanner）—— 检测逻辑不可复用
- 配置驱动（像传统 DLP）—— 不灵活，Hecate 需要 code extensibility

**理由**：Presidio、Bedrock、llm-guard 全部采用三层分离。检测层只管"有什么"，策略层管"做什么"，执行层管"怎么做"——各层独立测试。Hecate 已有的 Platform SPI 模式（EvaluatorABC、ChannelABC）也是三层分离，架构一致。

### D2: PresidioRecognizer 作为可选依赖

**选择**：通过 `[security]` extra 引入 presidio-analyzer + presidio-anonymizer + spacy。base 安装不依赖。

**替代方案**：
- Presidio 作为硬依赖——增加 base 安装体积（torch + transformers）
- 不集成 Presidio——失去 NER 能力，只能 regex

**理由**：AWS Bedrock Guardrails 用 ML 检测（硬依赖其基础设施），Microsoft Presidio 是可选开源方案。Hecate 与其硬依赖 Presidio，不如让它可选——regex 基线已覆盖 90% 用例，Presidio 作为增强。

### D3: EgressFilter ABC 而非 MCP 专用类

**选择**：定义通用 EgressFilter ABC，DLPEgressFilter 是一个实现。MCP client 注入 egress_filters 列表。

**替代方案**：
- HecateMCPClient 直接调用 DLPScanner——硬耦合
- MCP 专用 EgressFilter——无法复用到 A2A / Webhook

**理由**：阿里 AgentScope 已用 middleware/filter chain 模式。Hecate 多跳架构有 4 个 egress 点（MCP 响应、Tool 输出、LLM 输出、Channel 输出），通用 ABC 可复用。Claude Code PostToolUse 不能 block 是因为框架层架构——Hecate 自己代码层可避免。

### D4: 三级 override + is_locked 显式字段（最具体覆盖）

**选择**：org → workspace → agent 三级 scope，DLPPolicyModel.is_locked 字段控制下级是否可覆盖。

**替代方案**：
- "最严格生效"（Purview / Forcepoint 模式）——只能收紧不能放宽
- 纯最具体覆盖（无 is_locked）——安全团队无法设硬约束

**理由**：Hecate 是多租户 SaaS——workspace 经常需要放宽 org 规则（测试环境、假数据），但安全团队需要设不可逾越的红线（AWS key、private key）。结合两种需求：默认按 scope 优先级覆盖；is_locked=True 时下级不可覆盖。is_locked 显式字段业界无先例——但 Purview 的"most restrictive wins"和 Google DLP 的 org 级隐式优先已有类似机制，我们只是更显式。

### D5: 默认 fail-open + 内置默认规则

**选择**：未知 entity type 默认 ALLOW；部署时自动创建 secrets→BLOCK（locked）、PII→MASK、EMAIL→AUDIT 默认规则。

**替代方案**：
- 默认 fail-closed——部署初期误报率极高
- 无默认规则——安全团队必须从头配置

**理由**：5/5 主流平台（Bedrock、Presidio、llm-guard、Dify、Forcepoint）默认非拦截或非强制。Forcepoint "Audit Only" 更是默认 action。默认规则 + fail-open 让 v1 可立即上线，无需安全团队手动配置。

### D6: Action 语义 = ALLOW / BLOCK / MASK / AUDIT

**选择**：四种 action 互斥（取最严）。AUDIT 表示"检测但不拦截，仅记录"。

**替代方案**：
- 三种 action（ALLOW/BLOCK/SANITIZE）——缺灰度机制
- 五种（含 REDACT 等）——过细，业界无此实践

**理由**：Bedrock NONE、Purview Audit only、Forcepoint Audit Only（默认!）、Symantec audit mode 5 家已用 AUDIT action。提供灰度上线能力：规则先 AUDIT 观察 → 验证准确率后 → MASK → BLOCK。

### D7: 流式扫描 = 增量 + 最终兜底

**选择**：StreamingDLPWrapper buffer 300 char / overlap 10 char 增量扫描；流结束后全量再扫一次。BLOCK 立即停流；MASK 流结束后发纠正消息（v1 短暂泄漏可接受）。

**替代方案**：
- 仅流结束后全量扫——用户等待长
- 仅增量扫——边界敏感数据漏检
- Holdback 模式（AgentScope 实时替换）——v2 实现，v1 简化

**理由**：300/10 参数直接采用 AgentScope 生产验证值（唯一有流式实现的同行）。Dify 用 100 char 不同值（无重叠）——AgentScope 的 overlap 处理边界更佳。v1 接受 MASK 短暂泄漏换取实现简单；BLOCK 级别（secrets、SSN）零泄漏。

### D8: DLP findings 复用 SecurityFindingModel

**选择**：rule_name 前缀 `dlp:` 写入 SecurityFindingModel。不新建 DLPFindingModel。

**替代方案**：
- 新建 DLPFindingModel——数据冗余，SIEM pipeline 需双写
- 用 EventStore——非结构化，查询不便

**理由**：SecurityFindingModel 已有 org_id/workspace_id/user_id/severity/source_event 字段，完全覆盖 DLP finding 需求。Purview 和 Forcepoint 都用统一 alert 表，不分模块建多个。

### D9: EgressFilter 与 4 个 Hook 复用同一 DLPScanner

**选择**：所有 5 个边界点（InputSecurityHook、OutputSecurityHook、ToolResultSecurityHook、PreToolHook 增强、HecateMCPClient EgressFilter）共享一个 DLPScanner 实例。

**替代方案**：
- 各 hook 各自实现检测逻辑——重复
- DLPScanner per hook——无法共享 recognizer registry

**理由**：5 个出口点共用同一 Recognizer Registry + Policy Resolver，DB policy 一次配置全平台生效。Recognizer 状态（compiled regex、loaded Presidio model）只初始化一次。

## Risks / Trade-offs

[Presidio 首次加载慢] → 模型懒加载（首次 scan 才触发）；提供 `--security` flag 让用户在启动时预加载
[流式 MASK 短暂泄漏 PII] → v1 简化方案；v2 实现 holdback 模式（AgentScope 实时替换）
[is_locked 是新设计，业界无先例] → design.md 详细记录；proposal 标注为 "Hecate 创新 + 借鉴传统 DLP 'most restrictive wins' 机制"
[检测误报导致业务中断] → 默认 EMAIL/PHONE 用 AUDIT 灰度；内置 ruleset 选高置信度类型直接 BLOCK（secrets），低置信度类型 AUDIT（PII）
[多租户策略冲突] → 三级 override 规则明确（agent→workspace→org→default）；is_locked 阻止 workspace/agent 覆盖安全红线
[BDP Hallucination 产生虚假 PII] → DLPScanner 无差别扫所有文本；最终全量扫描兜底；hallucinated PII 也被 mask 不会泄漏
[Manager agent 输出 L LLM 推理层敏感数据] → PostLLM Hook 已 deanonymize + DLP 双重保护；Deanonymize 还原真实值，DLP 基于真实值做 egress policy
[Presidio NER 检测中国 PII 需要中文模型] → v1 只支持英文 spaCy 模型；中国 PII 通过 RegexRecognizer（中国身份证 regex）覆盖
[detection 性能开销] → 100ms（regex）+ 500ms（Presidio）= 600ms 上限；流式增量扫描额外 50ms/300 char；够用于 on-the-fly
[Registry DB 加载延迟] → 启动时预加载并缓存；policy 变更时 invalidate cache（无需重启）
[detect-secrets 误报 false positive] → 用户反馈机制（metadata_.feedback）记录；调整规则或 ALLOW 覆盖

## Migration Plan

### Phase 1: 部署前
- 新增 `[security]` extra，文档说明 Presidio + detect-secrets 安装方式
- 更新 docs/security/dlp.md 解释 4 个边界点 + 策略配置

### Phase 2: 灰度上线（默认规则）
1. 部署 chang后，迁移自动创建 3 张表 + 内置默认规则（secrets locked、PII mask、EMAIL audit）
2. DLPScanner 默认启用（`DLP_ENABLED=True`），但内置规则中 EMAIL/PHONE/CHINA_ID 是 AUDIT，不拦截
3. secrets（AWS_KEY, PRIVATE_KEY 等）立即 BLOCK——这些是硬红线
4. 监控 SecurityFindingModel 中 `rule_name LIKE 'dlp:%'` 的 finding 数量

### Phase 3: 收紧规则（1-2 周后）
1. 安全团队 review findings：真阳性率？误报率？误报 entity types？
2. 调整内置规则：EMAIL AUDIT → MASK → BLOCK（按风险）
3. 工作空间管理员根据业务调整 workspace 级规则
4. 测试环境 workspace 设置 SSN→ALLOW（测试数据）

### Phase 4: 全量启用（2-4 周后）
1. 所有 entity type 从 AUDIT 升到 MASK 或 BLOCK
2. `DLP_FALLBACK_OPEN=False` 启用严格默认（高安全租户）
3. MCP 响应 DLP 启用（之前 v1 默认启用）
4. 流式 DLP 启用（之前 v1 默认启用）

### 回滚策略
- `DLP_ENABLED=False`：所有 hook 跳过 DLPScanner，回退到原 PIIAnonymizer 行为（兼容模式）
- `DLP_MCP_RESPONSE_FILTER=False`：关闭 MCP EgressFilter
- 数据表保留，不删除（policy 配置可复用）
- 灰度期间任何问题可立即回滚

## Open Questions

1. **中国 PII NER 支持**：当前 RegexRecognizer 用 regex 覆盖身份证/银行卡，但 NER（如中文姓名、地址）需要中文 spaCy 模型。是否在 v1 引入？
   - **倾向**：v1 仅 regex + 中文专用 regex 集合；中文 NER 留 v2

2. **detection 缓存**：同一文本多次扫描是否缓存？流式扫描同一文本会出现 100+ 次扫描。
   - **倾向**：v1 不缓存（简单），v2 加 LRU cache

3. **PreToolHook 增强**：是否在 v1 加 PreToolHook DLP 扫描（tool 参数检测）？
   - **倾向**：是——4 个不可信边界点必须全部覆盖