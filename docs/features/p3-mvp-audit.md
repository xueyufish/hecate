# P3 MVP 审计报告

> **目的**: P3 阶段完成度审计，为 MVP 版本迭代提供决策依据
> **范围**: P3 Trustworthy 阶段（重校准 + 插件生态调整后 124 项特性），含源码级实现状态验证
> **方法**: Feature catalog 分析 + 源码 grep/glob 扫描验证部分实现

---

## 概览

| 指标 | 数值 |
|------|------|
| P3 总特性数（重校准 + 插件生态调整后） | 124 |
| 已完成 ✅ | **84 (68%)**（含 5.13a Plugin Content Scanning；累计 1.3.18 ✅ + 1.3.19 ✅ + 8.20 ✅ + 5.5c ✅ + 5.13a ✅） |
| 未完成 | **40 (32%)**（原 58 − Drop 5 − Defer 15 + 新增 4 − 已完成 5 + 插件生态新增 2（5.5c ✅ / 5.13a ✅，双双交付）） |
| 已完成 Sprint | Sprint 4 (M4) ✅、Sprint 5 (M5) ✅ |
| 部分 Sprint | Sprint 6 (M6) — 约 79% 完成；Sprint 7 (M7) — 关键项已交付（1.3.19 / 8.20 / 1.3.18 / 5.5c / 5.13a + 5.5 T0 收紧 + Wave 1 三渠道 11.2/11.3/11.9），其余按 [roadmap.md](./roadmap.md) 推进 |
| 发布阻塞项 | 1 项关键 + 2 项强烈建议（1.3.19 ✅ + 8.20 ✅ + 1.3.18 ✅ + 5.5c ✅ + 5.13a ✅ 已交付，总开关默认开、插件生态已 go-live；剩 HITL durable audit + monotonic denial gating） |
| 有部分代码 | 2 项已有可观代码但标记为未完成 |
| 零代码 + 新增 | 34 项零代码（原 57 − Drop 5 − Defer 15 − 已完成 4）+ 6 项新增（1.3.18/1.3.19/8.20/6.27 + 5.5c/5.13a 插件生态调整） |

---

## 一、已完成特性（84 项）

### 2026-08 关键交付（重校准与插件生态调整驱动）

| # | 特性 | 说明 | 交付 |
|---|------|------|------|
| 1.3.19 | Event-Sourced Execution State (Log-as-Truth) | EventStore 上执行主路径 + derive_messages 投影 + checkpoint 降级物化缓存 + 运行时不变式 fail-stop（[ADR-030](../design/adr/030-event-sourced-execution-state.md)） | PR #72，2026-08-15 |
| 8.20 | Execution Replay & Debug Dashboard Phase 1 | 词表 `session→trace→event`；回放 API + Web UI + time-travel（纯消费侧，零 schema 变更） | PR #76，2026-08-16 |
| 1.3.18 | Dynamic Orchestration | 第 7 种多 agent 模式：COORDINATOR 节点 / TaskDAG 契约 / Magentic 双循环 / 三轴预算 / benefit-based 委派 / 五重隔离 / ORCHESTRATOR_DECISION+EVALUATION 事件（[ADR-032](../design/adr/032-dynamic-orchestration.md)；推迟项 1.3.18a P4 + UI companion P3） | PR #79，2026-08-17 |
| 5.5c | Agent Plugins 1.0 Standard Ingestion | 目录/git/zip 安装 + 封闭清单校验 + 固定位置发现 + SKILL.md→SkillModel（source/origin/pin-by-hash）+ 组件级信任分派 + stdio 9.4c 容器沙箱（`plugin/agent_plugins.py` 单 adapter） | PR #81，2026-08-18 |
| 5.13a | Plugin Content Scanning | 16 规则引擎（注入/secret/URL + Unicode 阈值 + NFKC/有界解码层 + 角色×规则矩阵）+ install/enable fail-closed + SecurityFinding 投影/ack；总开关翻默认开——插件生态 go-live | PR #83，2026-08-18 |
| 5.5 (enh) | T0 Tightening | ADR-029 "runtime artifacts never T0" 落码：`PythonEntryPolicy` 注入 `load_plugin`/`install_plugin_from_bundle` 双执行点，非第一方 `python:` entry SaaS 拒 / 自托管默认拒 + allowlist；install 前置拒绝 + 目录回滚（`openspec/changes/archive/2026-08-19-t0-runtime-plugin-tightening/`） | PR #84，2026-08-19 |
| 11.2 | Web Widget (Simplified) | iframe embed，浏览器直调 `/v1/chat/completions`（[ADR-031](../design/adr/031-web-widget-iframe-architecture.md)） | PR #78，2026-08-16 |
| 11.3 | Feishu (Lark) | ChannelABC 首个真实实现 | 2026-08-13 |
| 11.9 | Slack | ChannelABC 第二个真实实现 | 2026-08-13 |
| 13.6 | Version Upgrade | 零停机升级 | 已完成（PR 待合入） |
| 9.4d | Sandbox Container Pool | `services/sandbox/pool.py`（396 行）：预热、分配、回收、max-uses 退役、`PooledContainer`；`main.py`/`ports.py`/`tool/builtin.py` 已接线；测试 `tests/test_services/test_sandbox/test_pool.py`（`openspec/changes/archive/2026-07-29-sandbox-container-pool/`） | PR #42 |

### Enterprise Foundation — 100% ✅

| # | 特性 | 说明 |
|---|------|------|
| 10.3 | SSO/LDAP | OIDC、SAML、LDAP，首次 SSO 登录 JIT 用户配置 |
| 10.3b | SCIM Directory Sync | Azure AD/Okta 目录同步 |
| 10.4 | Quota Management | 租户级 API/存储/计算资源限制 |
| 10.6 | Authentication Service | JWT + Argon2 密码哈希 + API Key 验证 |
| 10.7 | Budget Management | 组织/工作空间/Agent 级预算，硬/软限额 |
| 10.8 | Enterprise Vault Integration | HashiCorp Vault、AWS/Azure/GCP Secrets Manager |

### Security Core ✅（已完成项）

> **注意**：以下安全特性已逐项完成。Security 域还有未完成项（9.2a、9.5a、9.8、9.11、7.10、9.1a、9.2、2.10b、9.6、7.7），详见"二"。9.10 Outbound DLP Engine 已完成（PR #58）。

| # | 特性 | 说明 |
|---|------|------|
| 9.4 | Execution Security | Tool approval、sandbox 隔离、4 级风险授权（LOW/MEDIUM/HIGH/CRITICAL） |
| 9.4a | Granular Operation Approval | 40+ 操作独立安全审批配置 |
| 9.4b | Trusted Workspace | 工作空间目录内文件操作自动放行 |
| 9.4c | Docker Sandbox Executor | Docker 容器执行，CPU/内存/网络限制，可配置超时 |
| 9.5 | Data Security | PII masking、Fernet 加密、per-agent 配置 |
| 9.12 | Environment Network Egress Control | per-environment allowedDomains/deniedDomains |
| 9.13 | Sandbox Enforcement Integration | EXECUTE_SANDBOX 路由到 DockerEnvironment |
| 9.14 | Structured Security Audit Pipeline | ToolDecisionModel、批量写入、REST API |
| 9.15 | Per-Execution Credential Scoping | 运行时凭证隔离，per-tool 注入 |
| 5.14 | Environment Security（伞形特性，自 5.9 改名避免与 Skill Loading 撞号） | P0 阶段完成（9.12-9.15） |
| 8.7 | Audit Logs + SIEM Pipeline | Webhook + Syslog + OCSF，SecurityFinding 持久化 |
| 9.10 | Outbound DLP Engine | 3 点扫描（Pre-LLM / Post-Tool / Pre-Memory）+ Redact/Block 两种模式 + 跨请求熵检测；DLPService 50+ 内置 recognizer（信用卡/SSN/邮箱/电话/JWT/AWS key 等）；MCP egress filter；Guardrail Hooks 集成（PreLLMHook on LLMWorker + PostToolHook on ToolWorker）；per-environment DLPConfigModel + REST API（PR #58） |

### Observability & Ops Center ✅（已完成项）

> **注意**：8.10 CI/CD Evaluation Gating 和 8.12 Agent Catalog Governance 未完成，详见"二"。

| # | 特性 | 说明 |
|---|------|------|
| 8.1a | Distributed Tracing | Trace→Span 层级追踪，OTel 兼容，failoverReason 属性 |
| 8.1b | Metrics Collection | 请求级 + Token 级指标采集 |
| 8.1c | Structured Logging | JSON 结构化日志，含 correlation ID |
| 8.2 | Real-Time Monitoring | 运行时状态、错误率、延迟 |
| 8.3 | Cost Dashboard | 按用户/Agent/会话统计 Token 和成本 |
| 8.5 | Prompt Version Management | 版本管理，tag 部署（production/staging） |
| 8.5b | Prompt Analytics & Diff | 版本对比、差异分析、per-version 性能分析 |
| 8.6 | Alerting | 错误率阈值、成本超预算、延迟异常告警 |
| 8.8 | Event Store | 12 种事件类型，版本追踪，replay 能力 |
| 8.9 | Unified Ops Center Dashboard | 聚合 8.9a/b/c 数据源的统一看板 |
| 8.9a | Agent Health Monitoring | Agent 舰队总览，per-agent 健康评分 |
| 8.9b | Conversation Analytics | 统计 + LLM 质量评分（v1+v2） |
| 8.9c | Tool Execution Analytics | p50/p95/p99 延迟、成功率、调用热力图 |

### Model Hub ✅（已完成项）

> **注意**：6.8 Multi-Auth Support、6.9 Provider Info Enhancement、6.10 Key Security Enhancement、6.12 Provider Auth State Management、6.13 Model Management UI Redesign 未完成，详见"二"。

| # | 特性 | 说明 |
|---|------|------|
| 6.4 | Cost Tracking (G8) | per-model/workspace 预算，z-score 异常检测，成本预测 |
| 6.11 | Model Classification (G6) | 多模态：modalities、capabilities、limits 结构化元数据 |
| 6.44 | Model Catalog | 能力徽章、Provider 对比矩阵、一键启用 |
| 6.45 | Model Lifecycle Manager | 版本注册表、staging 通道、晋升审批、弃用调度 |
| 6.8a | A/B Testing for Models | 流量分割，z-test 统计显著性计算 |
| 6.8b | Gray Release for Models | 加权路由，渐进式发布阶段 |
| 6.8c | Per-Prefix Circuit Breaker | per-provider 熔断器（CLOSED/OPEN/HALF_OPEN） |
| 6.8d | API Key Encryption | Fernet 加密存储 API Key |
| 6.8e | Model Provider CRUD | 数据库托管，加密存储，连通性测试 |
| O10+G4 | Model Monitoring Console | 延迟/成本/错误率趋势，drift detection |

### Multi-Agent ✅（已完成项）

> **注意**：2.10b Multi-Agent Trust Verification 未完成，详见"二"。

| # | 特性 | 说明 |
|---|------|------|
| 2.8 | Collaborative Conflict Handling | 4 策略：LWW/HUMAN/LOCK/NEGOTIATION |
| 2.9 | Unified Skill Registry | Tools/KBs/Workflows/Agents 统一为 Skills |
| 2.9a | Agent-Workflow Mutual Embedding | 递归嵌套，max_nesting_depth=3 |
| 2.10 | A2A Protocol | Google A2A v1.0，HTTP+JSON/gRPC，AgentCard 发现 |
| 2.10a | Signed Agent Cards | ES256、JWS 签名、JWKS 发布、密钥轮换 |

### Tool Platform ✅（已完成项）

> **注意**：5.4a MCP Gateway、5.4b MCP Streamable HTTP Transport、5.8 Enterprise System Integration Framework 未完成，详见"二"。

| # | 特性 | 说明 |
|---|------|------|
| 5.5 | Plugin System | plugin.yaml 加载、目录发现、lifecycle、config、permissions、REST API |
| 5.5 (TP5) | Plugin Type Taxonomy + SDK | 8 种插件类型、hecate.plugin SDK、CLI 模板生成 |
| 5.5b | Plugin Packaging | .hecate-plugin 打包、packaging CLI、上传/安装 UI |
| 5.6 | Tool Permission Control | available_when 门控、composable policy pipeline |
| 5.7 | Tool Caching | 工具结果缓存 |
| 5.4c | MCP Server Registry & Connection Mgmt | 连接池、自动重连、熔断器、多租户隔离 |
| 1.3.5i | Session Events + Tool Matchers | Settings 驱动 |

### Engine Resilience — 100% ✅

| # | 特性 | 说明 |
|---|------|------|
| 1.3.5f | Platform-Level Tool Gating | Worker 级 available_when 条件表达式 |
| 1.3.5g | Unified Exception Hierarchy | HecateError → EngineError/ChannelError/SecurityError |
| 1.3.5h | Framework-Level Auto-Retry | RetryStrategy ABC，指数退避 + jitter |
| 1.3.15 | Agent Environment | Environment 生命周期，memory directory |
| 1.3.15a | Environment Backend: Docker | DockerEnvironment，shell 执行，warm pool |
| 1.3.15b | Context Offloading | 保留溢出消息 |
| 1.3.15c | Sandbox Environment Mount | Environment 到 sandbox 的卷挂载 |
| 1.3.16 | Agent State Separation | Session state vs environment state 分离 |
| 1.3.17 | Agent Invocation Mode | agent_execute pipeline 对齐 + DSL invocation_mode |

### Platform SPI — 100% ✅

| # | 特性 | 说明 |
|---|------|------|
| 5.5a | Plugin SPI Core | PluginRegistry、PluginManifest、PluginLifecycle |
| 7.2-abc | EvaluatorABC | evaluate() 统一接口 |
| 11.1-abc | ChannelABC | receive/respond/stream，REST/WS/CLI 作为内置实现 |
| 10.3-abc | AuthProviderABC | authenticate() 统一接口 |
| 8.6-abc | NotifierABC | 合并到 ChannelABC 作为 NotificationChannelAdapter |
| 15.1 | i18n SPI | Locale 传递、消息目录加载、fallback 链 |

### Evaluation & Meta-Agent — 部分 ✅

| # | 特性 | 说明 |
|---|------|------|
| 7.2a | 40+ Built-in Evaluators | 正确性、幻觉、毒性等（增强项待补） |
| 7.6 | Regression Test Set | CI/CD 集成 |
| 7.7a | Failure Classification | 10 类 AgentRx 失败分类法 |
| 7.7b | Constraint Rule Generation | CRITICAL/HIGH/MEDIUM/LOW 优先级，注入 system prompt |
| 7.7c | Constraint Injection | 防止同类失败再次发生 |
| 13.9a | Meta-Agent Scheduler | 轻量 async 调度器，可配置间隔 |
| 13.9b | Garbage Collector Agent | 扫描过期会话和孤儿 checkpoint |
| 13.9c | Configuration Drift Detection | 实际 vs 预期配置对比，HIGH/MEDIUM/LOW 影响分级 |
| 13.9d | Compliance Checker Agent | ruff 代码风格 + 安全配置审计 |
| 13.10 | Temporal Conflict Resolution | 4 策略（Saga 补偿增强待补） |

### 其他已完成

| # | 特性 | 说明 |
|---|------|------|
| 4.4 | Knowledge Memory (L4) | 长期知识归档，可搜索 |
| 4.6 | Memory Isolation | 用户/Agent/会话级记忆隔离 |

---

## 二、未完成特性（40 项）

> 本部分包含：A/B/C 三类明细与代码扫描验证。推进顺序不在此跟进，见 [roadmap.md](./roadmap.md)。

### A 类：标记 ✅ 但有遗留增强项（8 项）

这些特性主体功能可用，但计划中的增强功能尚未实现：

| 特性 | 主体状态 | 遗留增强 | 影响 |
|------|----------|----------|------|
| 7.2a Evaluators | ✅ 40+ 评测器 | OE8：三维结构化看板（Effectiveness/Efficiency/Safety） | 中 — UI 组织 |
| 7.2a Evaluators | ✅ | OE9：Reasoning Efficiency Evaluator（Pregel superstep + Tool Call Span） | 中 — 新评测器 |
| 8.6 Alerting | ✅ 基础告警 | O9：Incident Management Console（确认/升级/静默） | 中 — 运维流程 |
| 8.9 Ops Dashboard | ✅ 聚合看板 | O8：Custom Dashboard Builder（拖拽式编辑器） | 低 — 个性化 |
| 8.9b Conversation Analytics | ✅ 统计 + 评分 | OE5：Topic Clustering + 低分系统性分析 | 中 — 分析深度 |
| 13.10 Temporal | ✅ 4 策略 | E4：Saga/Compensation Pattern（多步回滚） | 中 — 分布式事务 |
| O10+G4 Model Monitoring | ✅ 延迟/成本/漂移 | 质量回归检测（依赖评测数据） | 中 — 需评测数据 |
| 6.8 Multi-Auth | ✅ 7 种认证 | EF5：Zero Data Retention Policy | 高 — 合规 |

### B 类：已有部分代码（2 项）

标记为未完成但代码库中已有可观实现：

#### 5.4b MCP Streamable HTTP Transport — 🟡 客户端已完成

- **文件**: `src/hecate/services/mcp/client.py`
- **已实现**: MCP Client 使用 `streamablehttp_client` 连接外部 Streamable HTTP MCP Server
- **缺失**: Server 端实现（Hecate 暴露单一 `/mcp` 端点，POST/GET，SSE upgrade，无状态操作）
- **⚠️ 规范基线更新（复核）**: MCP 官方规范已发布 **2026-07-28 大修订**（无状态核心：移除 initialize/session；强制 Mcp-Method/Mcp-Name header 路由；MRTR 取代长连接 elicitation；Tasks 转扩展；Roots/Sampling 弃用，12 个月窗口）。Server 端实现应**直接按 2026-07-28 规范落地**，避免按 2025-03-26 实现后二次迁移——见 roadmap Sprint 6 `5.4b (upg)` 工作项
- **完成工作量**: 中（M）— 需 Server 端实现（按新规范）

#### ChannelABC Adapters — 🟢 接口 + 2 个真实实现已交付，其余 P5 deferred

- **文件**: `src/hecate/channel/adapter.py`（107 行 ABC）+ `channel/im/feishu.py` + `channel/im/slack.py`（2026-08-13 交付，Wave 1）
- **已实现**: 完整 ABC（`receive`/`respond`/`stream`、`ChannelCapabilities`、`CanonicalMessage`）+ 飞书/Slack 两个真实适配器
- **剩余**: 企微/钉钉/Discord/Telegram/微信/Web Widget 完整版 → **P5 deferred**（按需触发，见"三"之 Wave 节奏）
- **完成工作量**: 每个适配器小（S），Wave 2/3 按客户需求触发

### C 类：零代码 — 完全未启动

#### Evaluation Suite 扩展（6 项）— Sprint 7

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 7.2b | AI-Synthesized Evaluation Dataset | M | 7.2a ✅ |
| 7.2c | Online/Offline Evaluation Tasks | M | 7.2a ✅ |
| 7.2d | Trace Backflow Dataset | S | 7.2a ✅ |
| 7.2e | Evaluation Report Dashboard | M | 7.2a ✅ |
| 7.3 | Workflow Evaluation | M | 7.1 ✅ |
| 7.4 | Human Annotation | M | 7.2 ✅ |

> **说明**：`EvaluationDatasetService` 已存在（`services/evaluation/dataset_service.py`，333 行），但仅提供基础 CRUD — 以上高级功能均未实现。**Dropped**: 7.6a Prompt Auto-Optimization、7.6b Prompt Comparison — DSPy/IBM AgentOps (GEPA) 已标准化优化，LangSmith/Salesforce A/B API 已标准化对比，自建为负 ROI。

#### Security 增强（6 项）— Sprint 6

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 9.11 | Agent Runtime Protection | L | Guardrail Hooks ✅ |
| 7.10 | Automated Continuous Red Teaming | L | Security Testing ✅ |
| 9.1a | Injection Type Detection | S | Guardrails ✅ |
| 9.2 | System Prompt Leakage Protection | S | Output Security ✅ |
| 2.10b | Multi-Agent Trust Verification | M | A2A ✅ |
| 9.6 / 7.7 | Compliance Framework / Security Testing | 各 M | Security ✅ |

> **Dropped**: 9.2a Content Moderation（模型内置安全层 + OpenAI Moderation API 免费够用）、9.8 Full-Chain Network Security（TLS/WAF/API Gateway 属基础设施层职责，归部署指南）。

#### Multi-Channel Access（5 项 in P3）— Sprint 7

**调整**：Wave 1 = 11.2 简化版 + 11.3 + 11.9 Slack — **已全部交付**。Wave 2 = 11.4 企微 + 11.5 钉钉 + 11.9 Discord/Telegram（P5 deferred，按客户需求触发）。**11.6 / 11.10 / 11.2 完整版从 P3 挪到 P5 deferred**——按需触发，等明确客户需求。

| # | 特性 | 工作量 | 依赖 | Wave |
|---|------|--------|------|------|
| 11.2 | Web Widget (Simplified) ✅ *(ADR-031, PR #78)* | S | API ✅ | Wave 1 ✅ |
| 11.3 | Feishu (Lark) ✅ | M — ChannelABC 首个真实实现 | Channel SDK | Wave 1 ✅ |
| 11.4 | WeCom (WeChat Work) | S | 11.3 ✅ | **P5 deferred** |
| 11.5 | DingTalk | S | 11.3 ✅ | **P5 deferred** |
| 11.8 | Intent Recognition & Routing | M | Multi-Agent ✅ | 横切 |
| 11.9 | Slack ✅ / Discord、Telegram | M (Slack ✅) + S (D/T) | ChannelABC ✅ | Wave 1 (Slack ✅) + **P5 deferred (D/T)** |

#### Deployment & Operations（未完成 6 项）— Sprint 6

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 13.1a | Canary Release | M | EventStore ✅ |
| 13.1b | Agent Identity Service | M | Auth ✅ |
| 13.4 | Horizontal Scaling（仅剩 K8s scaling test harness，已决策归入 13.1） | M | Stateless ✅ + 13.4a ✅ |
| 13.17 | Environment Management & ALM Pipeline | L | 13.5 ✅ + 13.6 ✅ |
| 13.18 | API Management & Developer Portal | M | API ✅ |

> 13.5 Data Backup & Recovery ✅（PR #45）、13.6 Version Upgrade ✅、13.4a Distributed Session State Store ✅（5/5 + deprecation）均已移入"一"或标注完成。

#### Advanced Knowledge Base（3 项）— Sprint 7

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 3.3.2 | Incremental Update | M | RAG ✅ |
| 3.3.3 | Knowledge Quality Evaluation | S | Ragas |
| 3.4.1 | Batch Document Indexing | M | — |

> **Deferred to P5**: 3.1.2 OCR / 3.1.3 Table Extraction / 3.1.4 Layout Analysis（Docling/Unstructured/RAGFlow 已工业化，应集成不自建）、3.4.2 High-Throughput Retrieval（Qdrant 原生 sharding，部署指南即可覆盖）。

#### Canvas UI 增强（2 项）— Sprint 7

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 1.1.24 | Human Input / Form Node | M | interrupt() ✅ + Canvas ✅ |
| 1.1.25 | Trigger Node | M | Scheduled Tasks ✅ + Webhook ✅ |

> **Deferred to P5**: 1.1.18 / 1.1.19 / 1.1.20（Canvas Embedding / Skill Selector / Nested Graph）——无用户反馈的 Canvas 增强是投机；Dify Loro CRDT 协同编辑才是触发后的目标方向。

#### Memory Enhancement（7 项）— Sprint 7

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 4.3a | Memory Engine Enhancement | L | Memory System ✅ |
| 4.14 | Memory Importance Scoring | M | Memory System ✅ |
| 4.15 | Multi-Signal Fusion Retrieval | M | 4.14 |
| 4.16 | LLM-Managed Memory | M | ContextEngine ✅ |
| 4.17 | Memory Pressure Alert | S | Token Budget ✅ |
| 4.25 | Layered Memory System | M | 1.3.15 ✅ |
| 4.21 | Task Memory | M | Memory System ✅ |

#### AIP Capabilities（2 项）— Sprint 7

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 6.16 | NL2Agent / NL2Flow | M | Canvas ✅ + Graph DSL ✅ |
| 6.18 | Trace Annotation | S | EventStore ✅ + Audit ✅ |

> **Dropped**: 6.17 DSL Conversion Framework（MCP/A2A 标准化 + Salesforce Agent Script 开源，行业收敛于标准 agent 定义而非 DSL 兼容层）。
> **Deferred to P5**: 6.21 Decision Lineage——完整 decision lineage 需先建 Ontology 地基（数据+函数+应用版本绑定，Palantir 标准），原低估工作量。

#### Model Management UI — 整组 Deferred to P5

> 6.9 Provider Info Enhancement / 6.10 Key Security Enhancement / 6.12 Provider Auth State Management / 6.13 Model Management UI Redesign — 无用户前做 UI 是投机，等 13.1 SaaS 落地 + 真实用户反馈后再做（Dify 花 $30M 与数年做 UI 的前车之鉴）。

#### 其他（3 项）

| # | 特性 | 工作量 | 依赖 |
|---|------|--------|------|
| 3.2.4 | Reranking | M | Vector Search ✅ |
| 5.4a | MCP Gateway | M | MCP ✅ |
| 5.8 | Enterprise System Integration Framework | M | MCP ✅ |

### 代码扫描结果 — 部分实现检测（审计时点快照，其后 Wave 1 渠道等已交付）

对 `src/hecate/` 全部 60 项未完成特性进行源码 grep/glob 扫描：

| 搜索目标 | 模式 | 结果 |
|----------|------|------|
| Channel 适配器（Feishu/Slack 等） | `Feishu\|DingTalk\|WeCom\|Slack\|Telegram\|Discord\|WeChat` | ✅ **已交付（审计后）**: `channel/im/feishu.py` + `channel/im/slack.py`（2026-08-13 Wave 1）；其余 Wave 2/3 → P5 deferred |
| Canary/Redis | `canary\|CanaryRelease\|RedisAgentState\|version_upgrade` | ❌ 未找到（13.4a Redis session store 后续已交付，见"一"） |
| Backup/Recovery | `backup_recovery\|backup_records\|pg_dump\|BackupOrchestrator\|restore_backup` | ✅ **找到**: `services/backup/`（15 文件，完整备份/恢复/调度/验证实现，2026-07-31 PR #45） |
| DLP/RuntimeProtection/ContentModeration | `DLP\|DataLossPrevent\|RuntimeProtect\|ContentModeration\|InjectionDetect\|PromptLeakage` | ❌ 未找到（DLP 其后已交付 PR #58；RuntimeProtection 仍未实现） |
| NL2Agent/DecisionLineage/KnowledgeGraph | `NL2Agent\|NL2Flow\|DslConversion\|TraceAnnotation\|DecisionLineage\|knowledge_graph\|GraphStore\|community_detect\|Leiden` | ❌ 未找到 |
| Memory 增强 | `importance_score\|multi_signal\|memory_pressure\|layered_memory\|task_memory\|MemoryFlush` | ❌ 未找到 |
| Evaluation 扩展 | `synthesize_dataset\|online_eval\|offline_eval\|trace_backflow\|evaluation_report\|workflow_eval\|human_annotation\|prompt_optim` | ❌ 未找到 |
| OCR/Table/Layout | `ocr\|OCR\|table_extract\|layout_analysis\|RapidOCR\|Tesseract` | ❌ 未找到 |
| Reranking | `Rerank\|rerank\|reranking\|cross_encoder` | ❌ 未找到 |
| Canvas 节点 | `HumanInputNode\|FormNode\|TriggerNode\|trigger_node\|human_input\|form_node` | ❌ 未找到 |
| Per-Token-Type Auth | `PerTokenType\|TwoTierIdentity\|AppIdentity\|UserIdentity\|token_type_auth` | ❌ 未找到 |
| **Sandbox Pool** | `SandboxPool\|sandbox_pool\|container_pool\|warm_pool\|WarmPool` | ✅ **已完成**: `services/sandbox/pool.py`（396 行，PR #42 合入，已接线） |
| **MCP Streamable HTTP** | `StreamableHTTP\|streamable_http\|mcp_gateway` | ✅ **找到**: `services/mcp/client.py`（仅客户端） |
| **ChannelABC** | `ChannelABC\|class.*Channel.*Adapter` | ✅ **找到**: `channel/adapter.py`（107 行，仅 ABC；其后 Feishu/Slack 落地） |

**结论**: 审计时点 60 项未完成特性中 57 项零代码，仅 2 项有部分实现（MCP Streamable HTTP 客户端、ChannelABC 接口）。此后 Data Backup (13.5, PR #45)、Sandbox Container Pool (9.4d, PR #42)、Outbound DLP Engine (9.10, PR #58)、Wave 1 渠道（11.2/11.3/11.9）等相继交付，均已移入"一"。

---

## 三、发布影响分析

### 🔴 P0 — 发布阻塞（上生产前必须完成）

| 特性 | 阻塞原因 | 业务影响 | 工作量 |
|------|----------|----------|--------|
| **13.4** Horizontal Scaling | 13.4a Redis session store ✅ 已完成，但 K8s 多副本 scaling test harness 待实现（**决策：deferred to 13.1 SaaS Deployment**——引擎层无状态化已完成，K8s 侧验证随 13.1 production Helm chart 一起做最自然，参数调优也基于真实负载） | 无法水平扩展 | M |

**剩余工作量估算**: 13.6 已完成，13.4 仅剩 K8s scaling test harness（随 13.1 落地）。

> ✅ **13.4a 全部 5/5 changes 已完成**（archived, PR #46）+ **13.4a-6 AgentStateStore 软废弃**（archived, PR #47）。后续待办：`13.4a-7` 硬删除（≥ next minor）；K8s scaling test harness 归入 13.4。

> ✅ **13.5 Data Backup & Recovery** 已完成（PR #45）、**13.6 Version Upgrade** 已完成，不再阻塞发布。

### 🟡 P1 — 强烈建议（影响企业可用性）

| 特性 | 原因 | 市场影响 | 工作量 |
|------|------|----------|--------|
| **Completed-Feature Upgrades（1.3.4 HITL fail-closed + 9.4 内容感知门控）** | 概览"发布阻塞项"口径下的剩余两项：HITL durable audit（approval/asked + approval/decided 持久审计对）+ monotonic denial gating（单调拒绝不变式）——均消费 1.3.19 ✅ 富化事件日志。**实施中**：[openspec/changes/guardrail-upgrade-trio](../changes/guardrail-upgrade-trio/)（T0 接线已完成；T1/T2/T3 待做） | 生产审批审计链不完整、门控可被复活 | M + M |
| **Multi-Channel (11.x)** | ~~仅有 API 渠道~~ **Wave 1 已交付**（11.2 ✅ + 11.3 ✅ + 11.9 Slack ✅）；剩余 11.8 Intent Routing 为横切项，Wave 2/3 按需触发 | 中国企业 IM 市场基础覆盖（Wave 1）；企微/钉钉等长尾待客户需求 | 11.8 M；Wave 2/3 每渠道 S |
| **Evaluation Suite 基础 (7.2b/c/e)** | 无法度量 Agent 质量，无法向客户证明效果 | 无法建立企业信任 | M + M + M |

### 🟢 P2 — 可延后（增强型功能，不阻塞核心发布）

其余 33 项（重校准后）：Memory Enhancement（7 项）、AIP capabilities（NL2Agent、Trace Annotation）、高级安全（red teaming、injection detection、prompt leakage、trust verification）、Reranking、Incremental Update、Batch Indexing、MCP Gateway、Enterprise Integration、Deployment 若干（Canary、Horizontal Scaling 收尾、ALM、API Portal）、7.4a/7.5 等。

> **新增 4 项中 1.3.18/1.3.19/8.20 已交付；6.27 Browser Automation 仍在强烈建议档**——竞品分析定位为 P3 high，与 Multi-Channel Wave 1、Evaluation 基础同属一档，推进顺序见 [roadmap.md](./roadmap.md)。

**理由**: 这些是竞争差异化能力和增强功能。核心平台（Agent 创建、执行、多租户、安全基础、可观测性）在没有它们的情况下可以工作。

### 11.x Multi-Channel Wave 节奏

| Wave | Feature | 触发条件 | 时机 |
|------|---------|----------|------|
| Wave 1（P3 主线） | 11.2 简化版 + 11.3 飞书 + 11.9 Slack | 主动 | 全部交付 |
| Wave 2（P5 deferred） | 11.4 企微 + 11.5 钉钉 + 11.9 Discord/Telegram | 按客户需求触发 (企微/钉钉/Discord/Telegram 客户) | 暂停 |
| Wave 3 / P5 deferred | 11.2 完整版 + 11.6 微信 to-C + 11.10 Custom Channel SDK | 按客户需求 / 社区请求 | 不预定 |

**架构决策记录**：
- Web Widget（11.2）**不走 ChannelABC**——浏览器直接调 `/v1/chat/completions`，与 IM 渠道（webhook 推送）是不同的抽象层
- 11.3 飞书是 **ChannelABC 第一个真实实现**——会暴露当前 SPI 类型擦除（`raw: object`）等问题并推动 SPI 演进
- 调研覆盖业界 18 个项目（Dify / Intercom / Salesforce / IBM watsonx / Google Dialogflow + Gemini Enterprise / 阿里 AgentScope + OpenClaw / Hermes Agent / TorchV / openJiuwen / Hermes / Dify / DeerFlow / 美团 catpaw），确定业界两派（企业 CRM/SaaS widget 派 vs 新一代 agent 平台 IM-native 派），Hecate 走通用平台路线 = 两派都覆盖

---

## 附录：Sprint 进度详情

### Sprint 4 (M4) — P3 Core — ✅ 完成
- 韧性基础设施: ✅ Exception Hierarchy + Auto-Retry + Tool Gating
- ContextEngine Phase 1: ✅ LLMWorker pipeline
- Multi-Tenant + RBAC: ✅（SSO 在 Sprint 5 完成）
- 安全栈: ✅ Input/Output/Execution/Data
- 可观测性: ✅ Tracing/Monitoring/Cost/Alerting
- Platform SPI: ✅ Plugin SPI Core + EvaluatorABC

### Sprint 5 (M5) — P3 Enterprise — ✅ 完成
- Platform SPI: ✅ ChannelABC + AuthProviderABC + i18n
- A2A Protocol: ✅ + Signed Agent Cards
- Model Hub: ✅ Catalog + Lifecycle + Deployment + Fine-Tuning + Cost + Multi-Modal + Monitoring
- Enterprise Identity: ✅ SSO/LDAP + SCIM + Budget + Vault

### Sprint 6 (M6) — P3 Security & Ops — 约 79% 完成
- Ops Center: ✅ Dashboard + Health + Analytics + Tool Analytics
- SIEM Pipeline: ✅ Webhook + Syslog + OCSF
- Environment Security P0: ✅ 9.12 + 9.13 + 9.14 + 9.15
- Plugin System: ✅ Runtime + Taxonomy + Packaging
- Tool Platform: ✅ Permission + Caching + MCP Registry
- Agent Environment: ✅ Docker + Context Offloading + Sandbox Mount
- Data Backup: ✅ 13.5 full backup + restore + PITR + scheduling + verification (PR #45)
- Distributed Session State Store: ✅ 13.4a 全部 5/5 changes + 13.4a-6 deprecation（engine 抽象 + Redis/PG/Tiered + 生产接线 + 多副本验证 + EventStore PG wiring + AgentStateStore 软废弃，archived，PR #46 + #47）
- Outbound DLP Engine: ✅ 9.10 — DLPService + 50+ recognizer + MCP egress filter + Guardrail Hooks（PR #58）

**Sprint 6 未完成项**:
- ❌ CI/CD Evaluation Gating (8.10)
- ❌ Agent Catalog Governance (8.12)
- ❌ Agent Runtime Protection (9.11)
- ❌ Automated Red Teaming (7.10)
- ❌ Injection Detection (9.1a) / Prompt Leakage (9.2)
- ❌ Multi-Agent Trust Verification (2.10b)
- ❌ Compliance Framework (9.6)
- ❌ Enterprise Integration Framework (5.8)
- ❌ Deployment: Canary / Horizontal Scaling 收尾 / ALM / API Portal（Backup ✅ + Redis Session Store ✅ 13.4a 已完成 + AgentStateStore deprecated ✅ 13.4a-6 + Outbound DLP ✅ 9.10 + Version Upgrade ✅ 13.6）

### Sprint 7 (M7) — P3 Complete — 关键项已交付（已重排）

> 原 Sprint 7 计划（Advanced RAG 全家桶 / Knowledge Graph / Canvas 增强 / AIP 全家桶）已按竞品重校准缩减：KG 三项 + OCR/Table/Layout + Canvas 三项 + Model UI 四项 → P5 deferred；7.6a/b、6.17 dropped。替换为引擎架构 + 竞品硬缺口 4 项（1.3.19 ✅ → 8.20 ✅ → 1.3.18 ✅ → 6.27 ⏳ + 5.9 增强 ⏳），推进顺序见 [roadmap.md](./roadmap.md)。

- Engine Architecture: Event-Sourced State (1.3.19 Log-as-Truth) ✅ + Skill Provider Registry (5.9 增强) ⏳
- Plugin Ecosystem: Agent Plugins 1.0 Ingestion (5.5c ✅) + Content Scanning (5.13a ✅) + 5.5 (enh) T0 Tightening ✅（`openspec/changes/archive/2026-08-19-t0-runtime-plugin-tightening/`，ADR-029 "runtime artifacts never T0" 落为代码）
- Competitive Gaps: Dynamic Orchestration (1.3.18) ✅ + Run Replay Phase 1 (8.20) ✅ + Browser Automation (6.27) ⏳
- Completed-Feature Upgrades: 1.3.5i E3 瀑布中间件 / 1.3.4 HITL fail-closed / 9.4 内容感知门控 ⏳（实施中：[openspec/changes/guardrail-upgrade-trio](../changes/guardrail-upgrade-trio/)；发布视角优先，见"三"之 P1）
- Advanced RAG (rescoped): Reranking ⏳ / Incremental ⏳ / Knowledge Quality ⏳
- Multi-Channel: 11.2 简化版 ✅（Wave 1 收尾）+ 11.8 Intent Routing ⏳ + 11.16/11.17 ⏳
- Evaluation Suite: AI-synthesized / Online-Offline / Trace Backflow / Reports / Workflow / Human Annotation ⏳（7.6a/b dropped）
- Canvas: Human Input / Form Node ⏳ + Trigger Node ⏳（1.1.18-20 deferred）
- Memory: Engine Enhancement / Importance / Multi-Signal / Pressure Alert / Layered / Task Memory ⏳
- AIP: NL2Agent ⏳ / Trace Annotation ⏳（6.17 dropped、6.21 deferred）
- Auth: Per-Token-Type ⏳ / Two-Tier Identity ⏳
