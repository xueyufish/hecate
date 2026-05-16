# Hecate 顶层架构设计

> **版本**: v0.2
> **日期**: 2026-05-16
> **状态**: Draft
> **定位**: Hecate 企业级 Agent 平台的顶层架构设计，聚焦 P1 范围（月 1-3，19 个核心功能），P2-P4 用扩展点标注
> **调研基础**: 26 个项目调研 + 7 份综合报告 + 华为财经 Agent 建设指南 + RelayAgent 架构分析

---

## 架构决策记录 (ADR)

本节汇总所有已确认的架构决策，每条决策包含背景、结论和理由。

### AD-1: 编排模式 — Graph 编排为主，三层 Agent 为预设模板

- **背景**: 需要确定 Agent 编排的基本范式 — 固定分层 vs 通用图 vs 代码优先
- **结论**: Graph 编排为主，三层 Agent（Guard→Plan→Sub-Agent）作为预设 Workflow 模板
- **理由**: 三层 Agent 是固定 Graph 拓扑的特例，不是替代。渐进式复杂度（对话→三层Agent模板→画布→代码SDK），每层向上兼容
- **竞品对比**: 华为财经 Agent 硬编码 6 种编排模式；RelayAgent 三层 Agent 硬编码；Hecate 用通用 Graph 统一，三层 Agent 只是模板

### AD-2: 系统分层 — 五层架构

- **背景**: 需要确定系统的分层方式，平衡模块化和复杂度
- **结论**: 五层架构 — 接入层→编排层→执行引擎层→能力服务层→基础设施层
- **理由**: 编排层（做什么）和执行引擎层（怎么跑）解耦，便于独立演进和替换
- **竞品对比**: Dify 四层（接入→编排→服务→数据）；华为四层（接入→服务→引擎→基础设施）；Hecate 增加独立的编排层

### AD-3: Session 状态 — Checkpoint 持久化 + 内存缓存

- **背景**: 需要确定 Session 的状态管理策略 — 有状态 vs 无状态 vs 混合
- **结论**: P1 实现 Checkpoint 持久化接口（PostgreSQL），允许内存缓存加速 hot path，缓存一致性 P2 优化
- **理由**: Checkpoint 接口必须 P1 就有（支持断点恢复、时间旅行调试），内存缓存是性能优化
- **竞品对比**: RelayAgent 严格无状态（Session V2 + 事件溯源重建）；LangGraph Checkpoint 可选；Hecate 取中间路线

### AD-4: Skill 系统 — P1 核心 / P2 增强

- **背景**: Skill 系统是 Hecate 的核心差异化之一，需要确定 P1/P2 边界
- **结论**: P1 实现 SKILL.md 格式 + 多源发现（system/user/project）+ 按需加载；P2 增加知识图谱自动选择 + Play 咨询模式 + 远程源 + 角色叠加
- **理由**: P1 的 Skill 核心足以支撑三层 Agent 模板的 Sub-Agent 动态加载；知识图谱等增强功能不是 P1 阻塞项
- **竞品对比**: RelayAgent 4-Tier Skill + 知识图谱 + Play 模式；Claude Code SKILL.md 格式；Hecate P1 覆盖 Claude Code 模式，P2 覆盖 RelayAgent 增强

### AD-5: 执行引擎分布式 — Worker Pool 渐进式

- **背景**: 当前功能清单中未明确多进程/分布式执行的支持路径，需要确定演进方向
- **结论**: Pregel 调度器保持单进程（轻量），Node 实际执行分发到 Worker Pool。P1 进程内线程池 → P2 跨进程 Worker → P3 可选 Temporal 后端
- **理由**: Channel/Checkpoint 所有权在 Scheduler（简单），Worker 无状态可扩展（弹性），演进到 P3 时替换调度器为 Temporal（兼容）
- **关键约束**: Worker 只接收 Channel 只读快照，不直接修改 Channel；interrupt 通过 WorkerResult 通知 Scheduler；Worker 无状态可被重新调度
- **竞品对比**: LangGraph OSS 单进程，Cloud 版分布式（闭源）；Dify 单进程 + Celery 异步任务；Hecate 从 P1 就设计 Worker 接口，渐进式扩展

### AD-6: 记忆系统分级 — 四级记忆渐进式实现

- **背景**: 调研报告 `03-memory-system.md` 已设计完整四级记忆（L1-L4）+ 华为三工序（构建→演化→检索）+ Consolidation Agent，但全部放入 P1 过重。BGE Embedding 补调确认 P1 默认选型为 BGE-M3
- **结论**: 四级记忆按优先级渐进实现 — P1 做 L2 简化版 + L4（即 RAG），P2 做 L1 + L2 完整 + L3，P3 做 Consolidation Agent + 实体图谱

| 级别 | P1 | P2 | P3 |
|------|-----|-----|-----|
| **L1 工作记忆** | ❌ 用 system_prompt 拼接代替 | ✅ 完整 MemoryBlock（命名块、Token 预算、Agent 可编辑） | 只读块、并发控制 |
| **L2 会话记忆** | ✅ 对话历史 + 基础截断（超长截最早消息） | ✅ 完整压缩管道（snip→microcompact→autocompact） | 413 紧急压缩 |
| **L3 用户记忆** | ❌ 不做 | ✅ Mem0 式提取 + pgvector + 多信号融合排序 | Consolidation Agent + 实体图谱 |
| **L4 知识记忆** | ✅ 即 RAG 管道，不单独列为"记忆" | 增强：混合检索 + 重排 | 记忆-RAG 联合检索 |

- **L4 RAG Embedding 选型（BGE-M3 补调结论）**: P1 默认使用 **BGE-M3**（569M 参数, 1024 维, 8192 token 长度, MIT 协议），核心优势：
  - Dense + Sparse + ColBERT 三合一混合检索，与 Qdrant dense + sparse 双向量天然匹配
  - 100+ 语言覆盖，一个模型解决中英日韩等多语言知识库
  - LlamaIndex 原生支持 + Qdrant 混合索引配置
  - FP16 部署仅 ~1.5 GB 显存，开发环境 CPU 亦可运行
- **P1 RAG 管线**: Docling 解析 → 文本分片(512-1024 tokens) → BGE-M3 encode(dense+sparse) → Qdrant 混合索引 → Query encode → Hybrid Search → Top-K → LLM
- **P2 增强**: bge-reranker-v2-m3 精排 + bge-code-v1 代码知识库
- **理由**: P1 目标是"能跑通完整 Agent 应用"，多轮对话需要 L2（至少简化版），RAG 已在功能清单中（即 L4）。L1 工作记忆块和 L3 跨会话记忆是好体验但不是 P1 阻塞项
- **竞品对比**: Letta 四级全部一次性实现（学习曲线陡）；Mem0 只做 L3（单点）；Claude Code 只做 L2 压缩（无持久记忆）；Hecate 渐进式，每期交付可用价值

### AD-7: 多 Agent 编排 — 所有模式统一为 Graph 模板，渐进提供

- **背景**: 功能清单列出 10 种多 Agent 编排模式（层级/移交/流水线/广播/对等选择/专家团/中央控制器等），需要确定如何在 Graph 框架中统一表达以及 P1/P2/P3 边界
- **核心洞察**: 所有编排模式都可以用 Graph 表达 — 层级 = agent 节点嵌套；移交 = Command(goto)；流水线 = 线性链；广播 = fan-out/fan-in；对等选择 = LLM 路由循环。因此不需要硬编码任何模式，统一为 Graph 模板
- **结论**: 所有模式都是预编译 Graph 模板，按阶段渐进增加模板库

| 阶段 | 提供的模式 | 说明 |
|------|-----------|------|
| **P1** | 层级委派 | 已通过三层 Agent 模板（Guard→Plan→Sub-Agent）覆盖，无需额外工作 |
| **P2** | 移交（Handoff）+ 多 Agent 可视化编排 | 移交是最常见场景（客服转专家、通用转垂直）；画布是 P2 核心交付物 |
| **P3** | 流水线 + 广播 + 对等选择 + 专家团 + 中央控制器 + 冲突处理 + Agent 间通信 | 逐步增加预设模板，每个模板就是一个预编译的 Graph |

- **实现方式**: `agent` 类型节点是统一原语（引用另一个 Agent，状态映射 parent→child），所有模式通过组合 agent 节点 + condition 节点 + Command 构建不同的 Graph 拓扑
- **竞品对比**: Coze 硬编码 Multi-Agent 模式；AutoGen 提供 GroupChat 抽象但不可视化编排；CrewAI 支持 Sequential/Hierarchical 但不支持自由拓扑；Hecate 用通用 Graph 统一所有模式，画布可视化

### AD-8: 安全与授权 — 横切关注点，Plugin 扩展点实现，渐进增强

- **背景**: 安全是横切关注点（跨越接入层→编排层→引擎层→服务层），功能清单中涉及四级风险授权、审批作用域、安全护栏、审计日志、沙箱隔离。LLM Guard + OWASP LLM Top 10 补调完成后，安全分层架构和风险覆盖更加明确
- **结论**: 通过 Plugin 系统的 Decision（决策）和 Observe（观测）扩展点实现安全策略，不硬编码在引擎中。渐进增强：

| 阶段 | 安全能力 | 说明 |
|------|---------|------|
| **P1** | 基础内容过滤 + API Key 认证 + LLM Guard 四 Scanner | 最低安全基线：防注入/泄露 + 简单认证 |
| **P2** | 四级风险授权 + Once/Session 作用域 + 沙箱隔离 | 工具调用授权确认；代码执行容器隔离 |
| **P3** | 完整护栏（输入/输出/检索/执行四层）+ Project/Global 作用域 + 审计日志 + SSO/LDAP | 企业级安全合规 |

- **安全分层架构（LLM Guard + NeMo Guardrails 互补）**:

```
用户请求
  │
  ▼
┌─────────────────────────┐
│  NeMo Guardrails (外层)  │  ← 对话流程、话题约束、行为边界
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  LLM Guard (内层)        │  ← 内容级安全扫描
│  P1 Input Scanners:      │
│  - PromptInjection       │
│  - Anonymize (PII)       │
│  - Secrets               │
│  - Toxicity              │
└────────────┬────────────┘
             │
             ▼
        LLM 推理
             │
             ▼
┌─────────────────────────┐
│  LLM Guard (内层)        │
│  P1 Output Scanners:     │
│  - Sensitive (PII)       │
│  - Toxicity              │
└────────────┬────────────┘
             │
             ▼
┌─────────────────────────┐
│  NeMo Guardrails (外层)  │  ← 输出合规检查
└────────────┬────────────┘
             │
             ▼
         用户响应
```

- **OWASP LLM Top 10 (2025) 风险映射**: P1 需重点覆盖 LLM01(Prompt Injection)、LLM02(敏感信息泄露)、LLM05(不当输出处理)、LLM07(System Prompt 泄露)、LLM10(无界消费)；P2 增加 LLM06(Excessive Agency — Agent 核心风险)和 LLM08(向量/嵌入弱点)；P3 完整覆盖全部 10 项
- **架构预留**: Tool/Agent 实体中已有 `risk_level`（LOW/MEDIUM/HIGH/CRITICAL）和 `approval_scope`（once/session/project/global）字段，P1 即存在但 P2 才强制执行。Plugin 五类扩展点（Transform/Decision/Observe/Lifecycle/Registration）中 Decision 用于授权决策，Observe 用于审计记录
- **P1 安全基线具体内容**:
  - LLM Guard PromptInjection Scanner（DeBERTa-v3 分类模型）— 对应 OWASP LLM01
  - LLM Guard Anonymize + Deanonymize（Presidio + BERT NER）— 对应 OWASP LLM02
  - LLM Guard Secrets Scanner（detect-secrets）— 对应 OWASP LLM02
  - LLM Guard Toxicity Scanner（输入+输出）— 基础内容安全
  - NeMo Guardrails 话题控制 — 对应 OWASP LLM06/LLM07
  - API Key 认证 + Rate Limiting — 对应 OWASP LLM10
- **竞品对比**: RelayAgent 四级风险授权从第一天强制执行（个人 Agent 场景）；OpenClaw 有 session lanes 冲突隔离；Hecate 面向企业，P1 最小基线 + LLM Guard 内层扫描，P2/P3 逐步加严

### AD-9: API 设计 — OpenAI 兼容 + Hecate 管理 API 双轨

- **背景**: Hecate 需要同时支持 OpenAI 兼容接口（现有工具无缝接入）和自有管理 API（Agent/Workflow/Session 等 CRUD）。需要确定路径设计和 P1 边界
- **结论**: OpenAI 兼容接口保持 `/v1/` 路径不做扩展，Hecate 特有能力走 `/api/` RESTful 路径，双轨并行

| API 类别 | 路径前缀 | P1 | P2 | P3 |
|---------|---------|-----|-----|-----|
| **OpenAI 兼容** | `/v1/chat/completions`, `/v1/models` | ✅ 核心对话 + 模型列表 | 工具调用流式 | 多模态 |
| **Agent 管理** | `/api/agents` CRUD | ✅ 创建/读取/更新/删除 | 版本管理 | 灰度发布 |
| **Workflow 管理** | `/api/workflows` CRUD | ❌ P1 用三层模板 | ✅ 完整 CRUD + 版本 | 导入/导出 |
| **Session 管理** | `/api/sessions` | ✅ 创建/列表/恢复 | 历史查询 | 管理后台 |
| **Knowledge Base** | `/api/knowledge-bases` | ✅ 创建/上传/检索 | 文档解析状态 | 自动同步 |
| **Tool 管理** | `/api/tools` | ✅ 列表（内置 + MCP 发现） | 自定义工具 CRUD | Tool 市场 |
| **Skill 管理** | `/api/skills` | ✅ 列表 + 加载 | CRUD + 远程源 | 知识图谱 |
| **Prompt 管理** | `/api/prompts` | ❌ P1 硬编码在 Agent 配置中 | ✅ 版本 + 标签 | A/B 测试 |
| **认证** | `Authorization: Bearer <api_key>` | ✅ API Key | OAuth 2.0 | SSO/LDAP |

- **设计约定**: `/v1/` 路径严格兼容 OpenAI 规范（不扩展字段名），`/api/` 遵循 RESTful + JSON + 统一错误格式（`{error: {code, message, details}}`），认证统一用 Bearer token
- **竞品对比**: Dify 自有 API + OpenAI 兼容（但兼容层不完整）；Coze 纯自有 API；LangGraph 无平台 API（只是 SDK）；Hecate 双轨设计，兼容层优先级最高

### AD-10: 前端画布 — React Flow + JSON DSL 双向同步

- **背景**: P2 核心交付物之一是可视化画布，需要确定技术选型和架构方式。P1 不需要画布，仅对话 UI
- **结论**: React Flow 作为画布引擎，自定义节点组件对应 Node 类型，JSON DSL 为单一 source of truth，画布是 DSL 的可视化编辑器

| 决策点 | 选择 | 理由 |
|-------|------|------|
| **画布库** | React Flow | 开源 MIT、社区活跃、自定义节点/边灵活、Mini Map + Controls 开箱即用 |
| **节点渲染** | 每种 Node 类型一个自定义 React 组件 | `llm`、`code`、`condition`、`tool`、`agent`、`subgraph` 各有不同 UI |
| **边类型** | 条件边用 label 标注（true/false）、普通边默认样式 | 和 Graph DSL 的 edge definition 对应 |
| **双向同步** | 画布操作 → JSON DSL → 编译器；JSON DSL 变更 → 画布更新 | 单一 source of truth 是 JSON DSL |
| **前端框架** | React 19 + TypeScript + Vite | 和 React Flow 生态一致 |
| **P1 前端** | 纯对话 UI（Chat 界面），无画布 | P1 不需要画布 |
| **P2 前端** | 对话 UI + Agent 配置器 + 画布 + 知识库管理 | 完整开发者界面 |

- **竞品对比**: Coze 自研画布绑定自家组件；Dify 用 React Flow 但节点类型硬编码；Langflow 用 React Flow 但只有 Python 执行；Hecate 用 React Flow + 自定义节点 + JSON DSL 双向同步，Graph-first 架构

---

## 第一章：产品定位与设计原则

### 1.1 一句话定义

Hecate 是一个**开源、自托管、模型无关、MCP-first** 的企业级 Agent 平台，让企业在其自有基础设施上构建、编排和运行 AI Agent 应用，拒绝供应商锁定。

### 1.2 Hecate 不是什么

- 不是一个 Agent 框架（如 LangGraph/AutoGen/CrewAI）— 框架是给开发者的库，Hecate 是给企业的平台
- 不是一个 SaaS 服务（如 Coze/百炼/千帆）— Hecate 是自托管的，数据主权在用户手中
- 不是一个 Agent 应用（如 RelayAgent/Claude Code）— Hecate 是让用户构建 Agent 应用的平台

### 1.3 核心差异化

| 差异化维度 | vs 商业平台（百炼/千帆/Coze） | vs 开源框架（LangGraph/AutoGen/Dify） |
|-----------|-------------------------------|--------------------------------------|
| **自托管优先** | 全部是 SaaS，不支持气隔部署 | LangGraph 需要 LangSmith；Hecate 完全自包含 |
| **MCP-first 架构** | MCP 是后加的节点类型；Hecate 以 MCP 为主要集成协议 | 没有框架原生支持 MCP Client+Server |
| **模型无关** | 各家绑定自有模型（Qwen/ERNIE/Doubao） | 无内置多 Provider 路由；Hecate 用 LiteLLM |
| **可视化画布 + 代码** | 有画布但扩展性差 | Langflow 有画布但只有 Python 执行；Hecate 是多语言 |
| **企业级记忆** | 基础或无记忆系统 | Mem0/Letta 是独立组件；Hecate 集成两者模式 |
| **开源核心** | 无一开源 | LangGraph 开源但依赖 LangSmith；Hecate 完全开源 |

### 1.4 六条设计原则

#### 原则一：开放优于封闭

- 模型无关：通过 LiteLLM 支持 100+ LLM Provider，不绑定任何模型厂商
- 协议开放：MCP（工具互操作）+ A2A（Agent 间互操作）作为一等公民
- 标准兼容：API 接口兼容 OpenAI 格式，Skill 格式兼容 Claude Code
- 拒绝供应商锁定：这是 Hecate 的核心品牌承诺

#### 原则二：可组合优于一体化

- MCP-first：所有外部能力通过 MCP 协议接入，而非硬编码集成
- 模块解耦：执行引擎、记忆服务、RAG 管道、工具系统独立可替换
- 预设模板：三层 Agent（Guard→Plan→Sub-Agent）是预设而非限制，用户可自定义任意编排
- 插件扩展：Plugin 系统提供 Transform/Decision/Observe/Lifecycle/Registration 五类扩展点

#### 原则三：可观测优于黑盒

- 全链路追踪：每个请求从接入到执行到响应，完整 Trace→Span→Generation 层级
- Checkpoint 可回溯：执行状态持久化，支持"时间旅行"调试
- 评估驱动：Agent 的每一步推理链必须可记录、可展示、可评估
- 成本透明：按用户/Agent/会话的 Token 和费用实时统计

#### 原则四：安全内建而非外挂

- 四级风险授权：LOW（自动放行）→ MEDIUM → HIGH → CRITICAL（不可自动批准），支持 Once/Session/Project/Global 四种作用域
- 安全护栏：输入/输出/检索/执行四层安全检查
- 审计日志：全量操作审计，满足合规要求
- 沙箱隔离：代码执行在加固容器中运行，网络/资源/文件系统隔离

#### 原则五：渐进式复杂度

用户不需要一开始就理解所有概念，按照使用深度自然递进：

```
Level 0: 对话模式 — 直接和 Agent 聊天（类似 ChatGPT）
Level 1: 三层 Agent 模板 — 一键启用 Guard→Plan→Sub-Agent，零配置
Level 2: 可视化画布 — 拖拽编排自定义工作流，确定性+不确定性混合
Level 3: 代码 SDK — 完全编程控制，高级用户
```

每个 Level 向上兼容 — Level 0 的对话可以无缝升级到 Level 1 的三层 Agent，Level 1 的模板可以在 Level 2 的画布中编辑。

#### 原则六：开发者体验优先

- 低代码 + 高代码双轨：画布和 SDK 是同一系统的两个界面，不是两个独立产品
- 热重载：Agent 配置和工作流修改后实时生效
- 一致性抽象：无论是画布操作还是 SDK 调用，底层执行引擎完全相同
- 完善的 CLI：命令行创建、测试、部署 Agent

---

## 第二章：系统分层架构

### 2.1 五层总览

```
┌─────────────────────────────────────────────────────────┐
│                    接入层 (Gateway)                       │
│  API Gateway · WebSocket/SSE · Web Widget · 多渠道适配    │
│  认证鉴权 · 限流 · OpenAI 兼容接口                         │
├─────────────────────────────────────────────────────────┤
│                    编排层 (Orchestration)                 │
│  Graph DSL 编译器 · 工作流管理 · 多 Agent 编排策略          │
│  预设模板（对话/三层Agent/固定工作流）· Human-in-the-Loop    │
├─────────────────────────────────────────────────────────┤
│                  执行引擎层 (Engine)                      │
│  Pregel 运行时 · Channel 状态 · Checkpoint 持久化          │
│  interrupt/Command · 子图组合 · 策略系统 · 流式输出         │
├─────────────────────────────────────────────────────────┤
│                  能力服务层 (Services)                    │
│  模型路由 · RAG 管道 · 记忆服务 · 工具系统(MCP)            │
│  Skill 管理 · 安全护栏 · 上下文管理                        │
├─────────────────────────────────────────────────────────┤
│                  基础设施层 (Infrastructure)              │
│  PostgreSQL · Qdrant · MinIO · LangFuse · 容器编排        │
│  认证授权(OAuth/OIDC/LDAP) · 日志 · 监控                  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 各层职责与接口

#### 接入层 (Gateway)

| 模块 | 职责 | P1 范围 |
|------|------|---------|
| API Gateway | REST API 路由、认证鉴权、限流 | ✅ OpenAI 兼容接口 + 基础 API Key 认证 |
| WebSocket/SSE | 流式响应推送 | ✅ SSE 流式输出 |
| Web Widget | 嵌入式聊天组件 | P2 |
| 多渠道适配 | 飞书/企微/钉钉/Slack 等 | P2 |
| 认证鉴权 | OAuth 2.0 / OIDC / LDAP | P1: API Key；P3: SSO/LDAP |

**接入层对下层接口**: 所有请求统一封装为 `ExecutionRequest`，透传到编排层。

```
ExecutionRequest {
    agent_id: UUID
    messages: List[Message]
    stream: bool
    config: ExecutionConfig     # 模型、温度、工具列表等
    context: RequestContext     # 用户信息、会话 ID、权限等
}
```

#### 编排层 (Orchestration)

| 模块 | 职责 | P1 范围 |
|------|------|---------|
| Graph DSL 编译器 | JSON/YAML → Compiled Graph | ✅ 基础 DAG 编译 |
| 工作流管理 | Workflow CRUD、版本管理 | P2 |
| 编排策略 | 确定性路由、LLM 路由、条件分支 | ✅ 路由分发 |
| 预设模板 | 对话模式、三层 Agent、固定工作流 | ✅ 对话模式 + 三层 Agent |
| HITL | 人工审批、暂停/恢复 | ✅ interrupt 机制 |
| 多 Agent 编排 | 10 种模式统一为 Graph 模板（AD-7） | P1: 层级（三层Agent模板）；P2: 移交+画布编排；P3: 流水线/广播/对等选择/专家团等 |

**编排层对下层接口**: 编译后的 `CompiledGraph` 交给执行引擎层运行。

```
CompiledGraph {
    nodes: Map[NodeId, NodeSpec]
    edges: List[EdgeSpec]
    channels: Map[ChannelName, ChannelSpec]
    entry_point: NodeId
    state_schema: TypeDict
}
```

**关键设计决策：三层 Agent 作为预设模板**

三层 Agent 不是编排层的硬编码路径，而是一个预定义的 Graph 模板：

```
Guard→Plan→Sub-Agent 模板 = CompiledGraph {
    nodes: {
        "guard": GuardNode,          # 安全检查、长任务规划
        "plan": PlanNode,            # 任务分解、Skill 选择
        "sub_agent": DynamicSubAgent # 根据 Skill 动态生成
    },
    edges: [
        START → "guard",
        "guard" → "plan",
        "plan" → condition("sub_agent" | END),
        "sub_agent" → "plan"         # 循环直到完成
    ]
}
```

用户选择"三层 Agent 模式"时，编排层自动实例化这个模板。用户在画布中可以看到并编辑这个 Graph。

#### 执行引擎层 (Engine)

| 模块 | 职责 | P1 范围 |
|------|------|---------|
| Pregel 运行时 | BSP 超步循环：读 Channel → 执行 Node → 写 Channel | ✅ |
| Channel 系统 | 状态管理：LastValue、Topic、PersistentTopic、Accumulator | ✅ |
| Checkpoint | 状态持久化到 PostgreSQL，支持断点恢复 | ✅ |
| interrupt/Command | Human-in-the-Loop：暂停等待、恢复继续 | ✅ |
| 子图组合 | 嵌套 Graph，状态映射（parent→child） | P2 |
| 策略系统 | Retry、Timeout、Cache、Fallback | ✅ Retry + Timeout + Fallback |
| 流式输出 | 7 种 stream 模式 | ✅ 4 种（values, updates, messages, debug） |

**执行引擎对下层接口**: 通过 Port 接口调用能力服务层。

```
EnginePorts {
    llm_invoke(messages, config) → Stream[Token]        # 模型调用
    tool_execute(name, args, context) → Result           # 工具执行
    memory_query(query, scope) → List[Memory]            # L3 用户记忆检索（P2）
    memory_store(key, value, scope) → void               # L3 用户记忆存储（P2）
    conversation_load(session_id) → ConversationHistory  # L2 会话记忆加载（P1）
    conversation_save(session_id, messages) → void       # L2 会话记忆保存（P1）
    knowledge_query(query, kb_ids) → List[Chunk]         # 知识库检索
    skill_load(name) → SkillContent                      # Skill 加载
    checkpoint_save(state) → CheckpointId                 # 状态持久化
    checkpoint_load(id) → State                          # 状态恢复
}
```

**关键设计决策：Checkpoint 持久化 + 内存缓存**

- Checkpoint 接口从 P1 就必须实现，所有状态变更都通过 Checkpoint 持久化
- 允许内存缓存作为 hot path 加速访问（缓存在写入成功后更新）
- 数据库写入可异步（WAL 先写日志，后台刷盘）
- 缓存一致性方案（TTL 失效 / 写穿透 / 事件通知）在 P2 优化

#### 能力服务层 (Services)

| 模块 | 职责 | P1 范围 |
|------|------|---------|
| 模型路由 | LiteLLM 集成，100+ Provider，降级/Fallback | ✅ 多模型接入 + 模型降级 |
| RAG 管道 | 文档解析→分块→Embedding→检索 | ✅ 基础 RAG（Docling + Qdrant） |
| 记忆服务 | 四级记忆（L1-L4）渐进实现 | P1: L2 简化版（对话历史+截断）；P2: L1 工作记忆+L2 完整压缩+L3 用户记忆；P3: Consolidation Agent |
| 工具系统 | 内置工具 + 自定义工具 + MCP Client | ✅ MCP 客户端 + 内置/自定义工具 |
| Skill 管理 | SKILL.md 格式 + 多源发现 + 按需加载 | ✅ 核心 Skill |
| 安全护栏 | 四层安全（输入/输出/检索/执行）+ 四级风险授权（AD-8） | P1: 基础内容过滤；P2: 四级授权+沙箱；P3: 完整护栏+审计 |
| 上下文管理 | 写入/选择/压缩/隔离 | P2 完整上下文管理；P1 基础压缩 |

#### 基础设施层 (Infrastructure)

| 组件 | 用途 | P1 范围 |
|------|------|---------|
| PostgreSQL | 关系数据 + Checkpoint + JSONB 元数据 | ✅ |
| Qdrant | 向量检索（Embedding + ANN） | ✅ |
| MinIO | 文件/文档/制品存储 | ✅ |
| LangFuse | 可观测性：追踪、成本、Prompt 管理 | ✅ 对话日志 + 基础追踪 |
| Docker Compose | 开发/单机部署 | ✅ |
| 认证授权 | API Key 认证 | ✅ API Key；P3: SSO/LDAP |

### 2.3 与参考架构的关键差异

| 维度 | 华为财经 Agent | RelayAgent | Hecate |
|------|--------------|------------|--------|
| **定位** | 企业内部建设指南 | 个人 Agent 应用 | 开源 Agent 平台 |
| **编排** | LangGraph 直接使用 | 三层 Agent 硬编码 | 通用 Graph + 三层 Agent 预设模板 |
| **执行引擎** | LangGraph Runtime | AgentScope Runtime | 自建引擎（借鉴 LangGraph 设计模式） |
| **前端** | AUI（华为内部） | Vue 3 + tinyVue | React Flow + React 19 |
| **部署** | 华为云内部 | 本地/单机 | Docker Compose → K8s → 气隔环境 |
| **模型** | MaaS + ModelArts | 单用户配置 | LiteLLM 100+ Provider |
| **无状态** | 未强调 | 严格无状态（Session V2） | Checkpoint 持久化 + 内存缓存 |
| **多租户** | 未涉及 | 未涉及 | P3 多租户 + RBAC |

---

## 第三章：核心概念模型

### 3.1 实体关系总览

```
Organization ─┬── Workspace ─┬── Agent ─┬── has Tools
              │              │          ├── has Skills
              │              │          ├── has MemoryBlocks
              │              │          ├── has KnowledgeBases
              │              │          └── runs Workflow
              │              │
              │              ├── Workflow ─┬── has Nodes
              │              │             └── has Edges
              │              │
              │              ├── KnowledgeBase ─── Document ─── Chunk
              │              │
              │              ├── Prompt (versioned)
              │              │
              │              └── Plugin
              │
              └── User ─── has Role
```

### 3.2 核心实体定义

#### Agent

Agent 是 Hecate 的核心概念 — 一个具备人设、模型、工具、知识和记忆的自主执行单元。

```python
class Agent:
    id: UUID
    name: str
    workspace_id: UUID

    # 身份
    persona: str                    # 系统提示词 / 人设描述
    model_config: ModelConfig       # 主模型 + 备用模型 + 参数

    # 能力
    tools: List[ToolRef]            # 关联的工具（内置 + 自定义 + MCP）
    skills: List[SkillRef]          # 关联的 Skill
    knowledge_bases: List[UUID]     # 关联的知识库

    # 记忆
    memory_blocks: List[MemoryBlock]  # L1 工作记忆（P2）
    memory_config: MemoryConfig       # 记忆策略（P2，含三工序配置）

    # 执行
    workflow_id: Optional[UUID]       # 绑定的工作流（None = 纯对话模式）
    mode: AgentMode                   # chat | three_layer | workflow

    # 安全
    risk_level: RiskLevel             # 默认风险等级
    approval_rules: List[ApprovalRule]

    # 元数据
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]
```

#### Workflow

Workflow 是一个可执行的有向图，定义了 Agent 的执行流程。

```python
class Workflow:
    id: UUID
    name: str
    workspace_id: UUID
    version: int

    # 图定义
    nodes: List[Node]
    edges: List[Edge]
    entry_node: NodeId

    # 状态定义
    state_schema: dict               # Channel 定义（字段名 → Channel 类型）
    state_defaults: dict              # Channel 默认值

    # 元数据
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime]

class Node:
    id: NodeId
    type: NodeType                    # llm | code | condition | tool | agent | subgraph | input | output
    config: dict                      # 节点配置（模型、工具、代码等）
    position: Optional[Position]      # 画布位置（前端用）

class Edge:
    id: EdgeId
    source: NodeId
    target: NodeId
    source_handle: Optional[str]      # 输出端口
    target_handle: Optional[str]      # 输入端口
    condition: Optional[str]          # 条件表达式（条件边）
```

#### Node 类型

| 类型 | 说明 | Channel 读写 |
|------|------|-------------|
| `llm` | LLM 推理节点 | 读取 messages/tools → 写入 response |
| `code` | 代码执行节点（沙箱） | 读取 input → 写入 output |
| `condition` | 条件分支节点 | 读取 state → 路由到对应分支 |
| `tool` | 工具调用节点 | 读取 tool_name/args → 写入 result |
| `agent` | 子 Agent 节点（引用另一个 Agent） | 映射 parent state → child state → 回传 result |
| `subgraph` | 子图节点（嵌套 Workflow） | 映射 outer state → inner state → 回传 result |
| `input` | 工作流输入节点 | 接收外部输入 |
| `output` | 工作流输出节点 | 输出最终结果 |

#### Tool

```python
class Tool:
    id: UUID
    name: str
    description: str                  # 自然语言功能描述（Agent 可理解）
    source: ToolSource                # builtin | custom | mcp

    # Schema
    parameters: dict                  # JSON Schema（输入）
    returns: dict                     # JSON Schema（输出）

    # 安全
    risk_level: RiskLevel             # LOW | MEDIUM | HIGH | CRITICAL
    approval_required: bool
    approval_scope: ApprovalScope     # once | session | project | global

    # MCP（当 source=mcp 时）
    mcp_server: Optional[str]
    mcp_tool_name: Optional[str]

class ToolSource(str, Enum):
    builtin = "builtin"               # 内置工具（代码执行、文件操作等）
    custom = "custom"                  # 用户自定义（API Schema）
    mcp = "mcp"                        # MCP 服务器提供
```

#### Skill

```python
class Skill:
    id: UUID
    name: str                         # 小写字母+连字符，如 "developer"
    description: str                  # 一句话描述
    source: SkillSource               # system | user | project

    # 内容
    instructions: str                 # SKILL.md body（Agent 指令）
    allowed_tools: List[str]          # 允许使用的工具列表
    metadata: dict                    # 元数据（~100 tokens）

    # 资源
    scripts: List[str]                # 可执行脚本路径
    references: List[str]             # 参考文档路径
    assets: List[str]                 # 资源文件路径

    # 加载控制
    max_tokens: int                   # 最大 Token 限制
    auto_load: bool                   # 是否自动加载

class SkillSource(str, Enum):
    project = "project"               # 项目级：{project}/.skills/
    user = "user"                     # 用户级：~/.hecate/skills/
    system = "system"                 # 系统级：平台内置
```

#### KnowledgeBase / Document / Chunk

```python
class KnowledgeBase:
    id: UUID
    name: str
    workspace_id: UUID
    embedding_model: str              # 使用的 Embedding 模型
    chunk_strategy: ChunkStrategy     # auto | fixed | semantic
    chunk_size: int
    chunk_overlap: int

class Document:
    id: UUID
    knowledge_base_id: UUID
    filename: str
    file_type: str                    # pdf | docx | md | html | ...
    parsing_status: ParsingStatus     # pending | parsing | completed | failed
    chunk_count: int

class Chunk:
    id: UUID
    document_id: UUID
    content: str                      # 分块文本内容
    metadata: dict                    # 元数据（页码、位置、标题等）
    embedding: List[float]            # 向量
```

#### Memory — 四级记忆系统

四级记忆渐进实现（AD-6）。L2 会话记忆 P1 必须有（多轮对话刚需），L1/L3 P2 引入，L4 等价于 RAG 管道。

```python
class MemoryBlock:
    """L1 工作记忆 — 上下文窗口中的命名区域（P2）"""
    id: UUID
    agent_id: UUID
    label: str                        # 如 "persona", "user_profile", "domain_context"
    content: str
    position: int                     # 在上下文中的位置
    limit: int                        # 最大 Token 数

class ConversationHistory:
    """L2 会话记忆 — 对话历史 + 自动压缩"""
    # P1: 简单消息列表 + 超长截断
    # P2: 完整压缩管道（snip→microcompact→autocompact）
    messages: List[Message]
    summary: Optional[str]            # 压缩摘要（P2）
    token_count: int                  # 当前 Token 计数

class Memory:
    """L3 用户/Agent 记忆 — 跨会话持久事实（P2）"""
    id: UUID
    content: str                      # 提取的事实/偏好/知识
    scope: MemoryScope                # user + agent + session
    memory_type: MemoryType           # semantic | procedural | episodic
    importance: float                 # 重要性评分
    access_count: int                 # 访问次数
    embedding: List[float]            # 向量
    created_at: datetime
    updated_at: datetime

# L4 知识记忆 = RAG 管道（KnowledgeBase + Document + Chunk），已在上方定义

class MemoryScope:
    user_id: str
    agent_id: UUID
    session_id: Optional[UUID]

class MemoryType(str, Enum):
    semantic = "semantic"             # 事实知识
    procedural = "procedural"         # 方法步骤
    episodic = "episodic"             # 事件经历
```

#### Conversation / Message / Session

```python
class Conversation:
    id: UUID
    agent_id: UUID
    user_id: str
    created_at: datetime

class Message:
    id: UUID
    conversation_id: UUID
    role: str                         # system | user | assistant | tool
    content: str
    tool_calls: Optional[List[dict]]  # 工具调用列表
    tool_call_id: Optional[str]       # 工具调用结果关联 ID
    metadata: dict                    # Token 用量、模型、延迟等
    created_at: datetime

class Session:
    """一次完整的 Agent 执行上下文"""
    id: UUID
    conversation_id: UUID
    agent_id: UUID
    status: SessionStatus             # active | interrupted | completed | failed
    current_node: Optional[NodeId]    # 当前执行到的节点
    checkpoint_id: Optional[UUID]     # 最新 Checkpoint
    created_at: datetime
    updated_at: datetime
```

#### Prompt

```python
class Prompt:
    id: UUID
    name: str
    workspace_id: UUID
    template: str                     # Prompt 模板（支持变量插值）
    variables: List[str]              # 模板变量列表
    version: int
    labels: List[str]                 # production | staging | development
    created_at: datetime
```

#### ResourceVersion — 通用资源版本化（P2）

Agent、Workflow、Prompt、Skill 等可版本化资源共享同一套版本管理机制，P2 统一实现，P3 扩展灰度发布和变更审批。

```python
class ResourceVersion:
    resource_type: str                # "agent" | "workflow" | "prompt" | "skill"
    resource_id: UUID
    version: int                      # 单调递增
    snapshot: dict                    # 该版本的完整配置快照（JSONB）
    change_summary: str               # 变更描述（自动生成或用户填写）
    changed_by: UUID                  # 操作人
    created_at: datetime
```

**适用范围**:

| 资源类型 | 版本化内容 | P2 能力 | P3 扩展 |
|---------|-----------|---------|---------|
| Agent | persona, model_config, tools, skills, kb_refs | 查看历史、回滚 | 草稿/测试/生产环境隔离 |
| Workflow | nodes, edges, state_schema | 已在 1.1.9 列出，对比、回滚 | 版本级回归测试 |
| Prompt | template, variables | 已在 8.5 列出，标签部署 | A/B 测试 |
| Skill | instructions, allowed_tools | 查看历史、回滚 | 远程源版本同步 |

#### Organization / User / Workspace

```python
class Organization:
    id: UUID
    name: str
    settings: dict                    # 组织级配置

class User:
    id: UUID
    org_id: UUID
    email: str
    name: str
    role: UserRole                    # admin | editor | viewer

class Workspace:
    id: UUID
    org_id: UUID
    name: str
    settings: dict                    # 工作空间级配置
```

### 3.3 存储设计

#### PostgreSQL 表映射

| 实体 | 表名 | 主要字段 |
|------|------|---------|
| Agent | `agents` | id, workspace_id, name, persona, model_config(JSONB), mode |
| Workflow | `workflows` | id, workspace_id, name, version, nodes(JSONB), edges(JSONB) |
| Tool | `tools` | id, workspace_id, name, source, parameters(JSONB), risk_level |
| Skill | `skills` | id, name, source, description, instructions(TEXT), allowed_tools(JSONB) |
| KnowledgeBase | `knowledge_bases` | id, workspace_id, name, embedding_model, chunk_strategy |
| Document | `documents` | id, kb_id, filename, file_type, parsing_status |
| Chunk | `chunks` | id, document_id, content, metadata(JSONB), embedding(vector) |
| MemoryBlock | `memory_blocks` | id, agent_id, label, content, position, limit |
| Memory | `memories` | id, content, scope(JSONB), type, importance, embedding(vector) |
| Conversation | `conversations` | id, agent_id, user_id |
| Message | `messages` | id, conversation_id, role, content, tool_calls(JSONB), metadata(JSONB) |
| Session | `sessions` | id, conversation_id, agent_id, status, current_node, checkpoint_id |
| Checkpoint | `checkpoints` | id, session_id, node_id, channel_state(JSONB), metadata(JSONB) |
| Prompt | `prompts` | id, workspace_id, name, template, version, labels(JSONB) |
| Organization | `organizations` | id, name, settings(JSONB) |
| User | `users` | id, org_id, email, name, role |
| Workspace | `workspaces` | id, org_id, name, settings(JSONB) |
| ResourceVersion | `resource_versions` | resource_type, resource_id, version, snapshot(JSONB), change_summary, changed_by |

#### 设计约定

- UUID 主键，支持分布式 ID 生成
- JSONB 列存储灵活元数据和配置
- 软删除（`deleted_at` timestamp）
- 租户隔离通过 `org_id` / `workspace_id` 外键（P3 启用 Row-Level Security）
- Alembic 管理 schema 演进
- Checkpoint 表按 session_id 分区（高频写入，P2 优化）

#### 与 LangGraph StateGraph 的映射

| Hecate 概念 | LangGraph 等价物 | Hecate 扩展 |
|------------|-----------------|------------|
| Workflow | `StateGraph` | JSON 序列化、可视化编辑、版本管理 |
| Node | `PregelNode` | 类型化输入输出、component metadata、8 种节点类型 |
| Edge | `add_edge` / `add_conditional_edges` | 可视化 source/target handles、类型校验 |
| Channel | channels/ | 扩展类型：PersistentTopic、Accumulator |
| Checkpoint | `BaseCheckpointSaver` | PostgreSQL 后端、多租户、审计 |
| Execution | `Pregel` runtime | 可选分布式后端（Temporal）、OTel 追踪 |
| Session | Thread ID | 完整生命周期管理（active/interrupted/completed） |

---

## 第四章：执行引擎设计

### 4.1 设计定位

执行引擎是 Hecate 的心脏。它接收编译后的 Graph，按 Pregel 模型执行，管理状态、处理中断、输出流式结果。

**核心设计决策**: 自建引擎，借鉴 LangGraph 的五个设计模式（Channel、Checkpoint、Pregel、interrupt/Command、子图），不依赖 LangChain 代码。

**代码量估算**: ~5000 行核心代码（借鉴 ~2500 行 + 自建 ~2500 行）。

### 4.2 Graph DSL

#### JSON Schema

```json
{
    "version": "1.0",
    "name": "my-workflow",
    "state": {
        "messages": { "type": "topic", "reduce": "append" },
        "current_plan": { "type": "last_value" },
        "iterations": { "type": "accumulator", "initial": 0 }
    },
    "nodes": {
        "guard": {
            "type": "llm",
            "config": {
                "model": "auto",
                "system_prompt": "You are a security guard...",
                "tools": ["content_filter", "risk_assessor"]
            }
        },
        "plan": {
            "type": "llm",
            "config": {
                "model": "auto",
                "system_prompt": "You are a task planner...",
                "tools": ["skill_selector", "task_decomposer"]
            }
        },
        "execute": {
            "type": "agent",
            "config": {
                "skill_ref": "{{ current_plan.selected_skill }}",
                "allowed_tools": "{{ current_plan.allowed_tools }}"
            }
        },
        "should_continue": {
            "type": "condition",
            "config": {
                "expression": "state.iterations < state.max_iterations AND state.current_plan.status != 'done'"
            }
        }
    },
    "edges": [
        { "source": "__start__", "target": "guard" },
        { "source": "guard", "target": "plan" },
        { "source": "plan", "target": "execute" },
        { "source": "execute", "target": "should_continue" },
        {
            "source": "should_continue",
            "targets": {
                "true": "plan",
                "false": "__end__"
            }
        }
    ]
}
```

#### Channel 类型

| 类型 | 语义 | 写入行为 | 典型用途 |
|------|------|---------|---------|
| `last_value` | 保留最后一个值 | 新值覆盖旧值 | 当前计划、当前状态 |
| `topic` | 消息流 | 追加（支持 reducer） | 对话消息列表、工具调用记录 |
| `persistent_topic` | 持久消息流 | 追加 + 持久化 | 审计日志、不可变事件流 |
| `accumulator` | 累加器 | 按指定函数聚合 | 迭代计数器、Token 用量统计 |

### 4.3 编译器

编译器将 Graph DSL（JSON）转换为运行时可直接执行的 `CompiledGraph`：

```
JSON DSL
  │
  ├── 1. Schema 校验
  │     └── 验证节点类型、边连接、Channel 定义
  │
  ├── 2. 依赖分析
  │     └── 构建节点依赖图、检测循环依赖（不允许的循环）
  │
  ├── 3. Channel 绑定
  │     └── 分析每个节点的读写 Channel，验证类型兼容
  │
  ├── 4. 编译优化
  │     └── 识别可并行节点、合并连续的纯函数节点
  │
  └── 5. 输出 CompiledGraph
        ├── nodes: Map[NodeId, CompiledNode]
        ├── edges: Map[NodeId, List[CompiledEdge]]
        ├── channels: Map[ChannelName, ChannelInstance]
        └── entry_point: NodeId
```

### 4.4 Pregel 运行时 + Worker Pool 分布式执行

执行引擎采用 Pregel/BSP（Bulk Synchronous Parallel）模型，节点执行分发到 Worker Pool：

**架构决策 AD-5**: Pregel 调度器保持单进程（轻量），Node 的实际执行（LLM 调用、工具执行、代码运行）分发到 Worker Pool。演进路径：P1 进程内线程池 → P2 跨进程 Worker → P3 可选 Temporal 后端。

```
┌──────────────────────────────────────────────────────┐
│                  Pregel Scheduler（单进程）            │
│                                                       │
│  1. READ: 各节点读取 Channel 当前值                    │
│  2. DISPATCH: 将就绪节点分发到 Worker Pool             │
│  3. AWAIT: 等待所有 Worker 返回结果                    │
│  4. WRITE: 各节点写入 Channel 新值                     │
│  5. CHECKPOINT: 持久化当前状态                         │
│  6. ROUTE: 根据条件边决定下一步                       │
│  7. CHECK: 是否还有就绪节点？                          │
│     ├── YES → 回到 Step 1                             │
│     └── NO → 执行结束                                 │
│                                                       │
│  随时可被 interrupt() 暂停                             │
└──────────────┬───────────────────────────────────────┘
               │ dispatch tasks
               ▼
┌──────────────────────────────────────────────────────┐
│                     Worker Pool                       │
│                                                       │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐              │
│  │Worker 1 │  │Worker 2 │  │Worker N │  ...          │
│  │(Thread) │  │(Thread) │  │(Thread) │              │
│  └────┬────┘  └────┬────┘  └────┬────┘              │
│       │            │            │                     │
│  P1: 进程内线程池 (asyncio/concurrent.futures)        │
│  P2: 跨进程 Worker (多进程 / 多容器)                  │
│  P3: 可选 Temporal / NATS 分布式后端                  │
└──────────────────────────────────────────────────────┘
```

#### Worker 接口

```python
class WorkerTask:
    task_id: UUID
    session_id: UUID
    node_id: NodeId
    node_type: NodeType              # llm | tool | code | ...
    node_config: dict
    channel_snapshot: dict            # 该节点需要的 Channel 只读快照
    deadline: Optional[float]         # 超时时间

class WorkerResult:
    task_id: UUID
    status: Literal["success", "error", "timeout", "interrupted"]
    output: dict                      # Channel 写入内容
    metadata: dict                    # Token 用量、耗时等
    error: Optional[ErrorInfo]
```

#### 渐进式扩展路径

| 阶段 | Worker 实现 | 通信方式 | 适用场景 |
|------|------------|---------|---------|
| **P1** | 进程内线程池 | 直接函数调用 / asyncio | 单机、低并发 |
| **P2** | 跨进程 Worker | 进程间通信（multiprocessing / Redis Queue） | 多用户并发、CPU 密集型工具 |
| **P3** | 分布式 Worker | Temporal Task Queue / NATS | 高可用、水平扩展、多租户 |

#### 设计约束

- **Channel 所有权在 Scheduler**: Worker 只接收 Channel 快照（只读），不直接修改 Channel。Worker 返回结果后，由 Scheduler 统一写入 Channel。
- **Checkpoint 由 Scheduler 控制**: Worker 不感知 Checkpoint，Scheduler 在所有 Worker 完成后统一持久化。
- **interrupt 由 Worker 触发**: Worker 执行中调用 `interrupt()` → 通过 `WorkerResult.status="interrupted"` 通知 Scheduler → Scheduler 暂停循环。
- **Worker 无状态**: Worker 不保存执行状态，重启后可被 Scheduler 重新调度（基于 Checkpoint 恢复）。

#### 执行流程示例：三层 Agent 模板

```
Superstep 1:
  READ:   messages = [user_input]
  EXECUTE: guard 节点 → 安全检查 + 风险评估
  WRITE:  guard_result = {safe: true, risk: LOW}
  CHECKPOINT: #1

Superstep 2:
  READ:   messages + guard_result
  EXECUTE: plan 节点 → 任务分解 + Skill 选择
  WRITE:  current_plan = {skill: "developer", tasks: [...]}
  CHECKPOINT: #2

Superstep 3:
  READ:   messages + current_plan
  EXECUTE: execute 节点（developer Sub-Agent）→ 执行任务
  WRITE:  messages.append(assistant_response), iterations += 1
  CHECKPOINT: #3

Superstep 4:
  READ:   iterations + current_plan
  EXECUTE: should_continue 节点 → 条件判断
  WRITE:  路由结果 = true/false
  → 如果 true: 回到 Superstep 2（Plan 重新评估）
  → 如果 false: 执行结束
```

### 4.5 Checkpoint 持久化

#### 设计原则

1. **每步必存**: 每个 Pregel superstep 完成后，Checkpoint 写入 PostgreSQL
2. **内存缓存**: 最近一次 Checkpoint 缓存在内存中，加速恢复
3. **异步写入**: 数据库写入可异步（先写 WAL，后台刷盘）
4. **不可变**: Checkpoint 一旦写入不可修改，支持时间旅行

#### Checkpoint 结构

```python
class Checkpoint:
    id: UUID
    session_id: UUID
    superstep: int                    # 第几轮超步
    node_id: NodeId                   # 当前执行的节点
    channel_state: dict               # 所有 Channel 的当前值
    pending_writes: List[Write]       # 待写入的 Channel 更新
    metadata: dict                    # 执行元数据（耗时、Token 等）
    created_at: datetime
```

#### 恢复流程

```
Session 被中断 → 用户发送恢复请求
  │
  ├── 1. 加载最新 Checkpoint
  ├── 2. 重建 Channel 状态
  ├── 3. 从中断点继续 Pregel 循环
  └── 4. 可选：用户修改状态后恢复（"时间旅行"）
```

### 4.6 interrupt / Command — Human-in-the-Loop

#### interrupt

节点可以调用 `interrupt(value)` 暂停执行，将控制权交还给用户：

```python
# 在 Agent 节点中
def approval_node(state):
    if state["risk_level"] == "HIGH":
        user_decision = interrupt({
            "type": "approval",
            "operation": state["pending_operation"],
            "risk_level": "HIGH",
            "message": "此操作需要您的审批"
        })
        if user_decision == "deny":
            return {"status": "cancelled"}
    return {"status": "approved"}
```

#### Command

节点可以返回 `Command` 对象，控制执行流程：

```python
# 移交给另一个 Agent
Command(goto="other_agent", update={"context": "handoff data"})

# 恢复中断的执行
Command(resume=value, update={"user_decision": "approved"})
```

### 4.7 子图组合（P2）

子图允许在一个 Workflow 中嵌套另一个 Workflow：

```
外层 Graph:
  ├── Node A
  ├── SubGraph B (内层 Graph)
  │     ├── Node B1
  │     ├── Node B2
  │     └── Node B3
  └── Node C

状态映射:
  外层 Channel → 内层 Channel（输入映射）
  内层 Channel → 外层 Channel（输出映射）
```

**命名空间隔离**: 子图内部的 Channel 使用命名空间前缀（如 `subgraph_b.messages`），避免与外层冲突。

### 4.8 策略系统

| 策略 | 说明 | P1 |
|------|------|-----|
| **Retry** | 指数退避重试，可配置最大次数、抖动、自定义判断 | ✅ |
| **Timeout** | 节点级超时、全局超时 | ✅ |
| **Fallback** | 主模型失败 → 备用模型 → 兜底响应 | ✅ |
| **Cache** | 工具调用结果缓存（相同参数直接返回） | P2 |
| **Rate Limit** | 工具/模型调用频率限制 | P2 |
| **Circuit Breaker** | 工具/模型熔断 | P3 |

### 4.9 流式输出

执行引擎支持 4 种流式模式（P1），后续扩展到 7 种：

| 模式 | 输出内容 | 用途 |
|------|---------|------|
| `values` | 每个 superstep 后的完整状态 | 调试、状态监控 |
| `updates` | 每个 superstep 的增量更新 | 进度展示 |
| `messages` | LLM 生成的 Token 流 | 前端实时显示 Agent 响应 |
| `debug` | 内部执行细节（工具调用、Channel 变更） | 开发调试 |
| `checkpoints` | Checkpoint 事件 | P2: 时间旅行 UI |
| `tasks` | 子任务状态变更 | P2: 多 Agent 任务追踪 |
| `custom` | 用户自定义流式输出 | P2: 自定义事件 |

### 4.10 与 LangGraph 的对比

| 维度 | LangGraph | Hecate |
|------|-----------|--------|
| **Graph 定义** | 纯代码（Python API） | JSON DSL + 画布 + 代码（三种入口） |
| **序列化** | 无原生序列化 | JSON Schema 原生支持 |
| **状态模型** | Channel（LastValue/Topic） | 扩展 Channel（+PersistentTopic/Accumulator） |
| **Checkpoint** | PostgreSQL/SQLite/内存 | PostgreSQL 优先，内存缓存加速 |
| **流式** | 7 种模式 | 4 种（P1）→ 7 种（P2） |
| **分布式** | 单进程（OSS） | Worker Pool 渐进式（P1 线程池 → P2 跨进程 → P3 Temporal） |
| **模型** | 绑定 LangChain BaseChatModel | LiteLLM 模型适配层，100+ Provider |
| **依赖** | 强依赖 langchain-core | 零外部框架依赖 |
| **DSL** | 无 | JSON/YAML → 编译器 → CompiledGraph |
| **可视化** | 无 | React Flow 画布（P2） |

### 4.11 引擎模块代码量估算

| 模块 | 行数 | 说明 |
|------|------|------|
| **Channel 系统** | ~500 | LastValue, Topic, PersistentTopic, Accumulator |
| **Checkpoint** | ~800 | PostgreSQL 后端 + 内存缓存 + 恢复逻辑 |
| **Pregel 运行时** | ~400 | BSP 超步循环 + 调度 |
| **Worker Pool** | ~600 | Worker 接口 + 线程池调度 + 任务分发/结果收集 |
| **interrupt/Command** | ~300 | 暂停/恢复/控制流 |
| **子图组合** | ~400 | 状态映射 + 命名空间隔离 |
| **Graph DSL 序列化** | ~1000 | JSON Schema + 编译器 |
| **FSM 语义** | ~300 | 显式状态机 + 转换 + 守卫 |
| **OTel 集成** | ~400 | traces/spans/metrics |
| **模型适配层** | ~300 | LiteLLM → Hecate 接口 |
| **工具系统** | ~500 | Tool 定义/注册/执行/MCP Client |
| **策略系统** | ~400 | Retry/Timeout/Fallback |
| **合计** | **~5900** | |

---

## 参考资料

| 资料 | 位置 | 说明 |
|------|------|------|
| 功能全集 | `docs/features/feature-catalog.md` | 156 功能点，P1-P4 |
| 调研追踪 | `docs/research/research-tracker.md` | 37/80 完成 |
| 架构决策总纲 | `docs/research/reports/00-architecture-decisions.md` | D1-D5 决策 |
| 执行引擎决策 | `docs/research/reports/01-execution-engine-decision.md` | D1 完整讨论 |
| 执行引擎报告 | `docs/research/reports/01-execution-engine.md` | 引擎综合分析 |
| RAG 架构 | `docs/research/reports/02-rag-knowledge.md` | RAG 分层设计 |
| 记忆系统 | `docs/research/reports/03-memory-system.md` | 四级记忆 |
| 基础设施 | `docs/research/reports/04-infrastructure.md` | 技术栈选型 |
| 功能对齐 | `docs/research/reports/05-feature-parity.md` | 差异化分析 |
| 华为财经 Agent 指南 | `docs/refs/md/huawei-finance-agent-guide-summary.md` | 华为四层架构+编排模式 |
| RelayAgent 架构 | `docs/refs/md/relay-agent-summary.md` | 三层Agent+无状态+Skill+授权 |
