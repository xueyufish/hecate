# Proposal: agent-memory-tools

## Why

Hecate 有完整的四层记忆底座(L1 blocks / L2 压缩 / L3 user memories / L4 knowledge memories),但 Agent 可调用的记忆工具面几乎为空:内置工具注册表无任何记忆工具,`KNOWLEDGE_TOOLS`(`knowledge_insert`/`knowledge_search`)是无人 import 的死代码,session-memory 规格 REQ-4 承诺的 `update_memory_block`/`search_user_memory` 从未落地为工具,跨会话语义检索过往对话的能力不存在。2026-09 业界调研(`docs/research/2026-09-memory-tools-survey.md`,24 个项目)表明:记忆工具面已是平台标配且工具签名有事实标准,而 Hecate 的 `MemoryProvider` 接缝仅有 `search()` 一个方法,不足以支撑工具层——所有下游设计都以此为地基。对应 feature catalog 的 4.16(伞形)、4.18、4.19、4.20。

## What Changes

- **MemoryProvider 协议扩展**:从单一 `search()` 扩展为 tier 分层契约(tier-1 通用检索 / tier-2 记忆检索 / tier-3 fact CRUD + prefetch/sync 生命周期钩子),缺失能力降级返回结构化错误而非崩溃;builtin(hecate-memory)从隐式实现降级为协议的默认实现。
- **Agent 记忆工具层(4.19 + 4.16 落地)**:注册 8 个内置工具——L1 编辑 `memory_replace` / `memory_insert` / `memory_rethink`(Letta 语义分层:精确替换 / 行插入 / 整块重写),L3/L4 事实 `memory_search` / `memory_add` / `memory_update` / `memory_forget`,transcript 检索 `conversation_search`;接入既有 builtin seeding 通路,同时把死代码 `KNOWLEDGE_TOOLS` 收编进新通路。
- **防误覆盖机制**:L1/L3/L4 记忆条目增加 `revision` 乐观并发(`expected_revision` 校验 + 冲突报错),新增 `memory_edit_log` 审计表记录每次记忆变更(before/after/操作者/会话);L3 的 ADD-only supersession(temporal 列)**不在本期**,推迟与 3.5.13 时序记忆合流。
- **会话召回存储(4.18)**:新建 transcript 级召回层——`recall_messages` 元数据表 + Qdrant `hecate_recall` 集合,后台 worker 在回合提交时对 user/assistant 消息写时向量化(AWS 风格 namespace:workspace/agent/session 分层,workspace 隔离一等公民),与 event retention 解耦(召回层是比事件活得更久的归档)。
- **检索升级门控(4.20,catalog 条目改写为 Retrieval Escalation)**:记忆/召回检索返回空或弱结果时注入 HintBlock(复用 4.13 预算 warn 词汇)引导改写重查;`conversation_search` 支持游标分页与 `exclude_session_ids` 迭代排除。MemGPT heartbeat 形态的对标退役,reference 更换为 OpenClaw Active Memory 门控形态。
- **prefetch 通道**:LLM 调用前按当前对话自动检索相关记忆注入上下文(与显式工具调用构成双通道),实现为 4.13 ContextProcessorChain 的记忆 prefetch processor,参与预算记账。
- **随带**:feature-catalog 4.18/4.19/4.20 条目更新 + openjiuwen 归属勘误(华为系,非中移动);调研文档 6 份入库。

Feature flags 默认全关(`MEMORY_TOOLS_ENABLED` / `RECALL_INDEXING_ENABLED` / `MEMORY_PREFETCH_ENABLED`),关闭时行为 byte-identical。无 **BREAKING** 变更:DB 迁移均为 additive。

## Capabilities

### New Capabilities

- `memory-provider-contract`:MemoryProvider 分层契约——tier-1/tier-2/tier-3 方法语义、能力缺失的结构化降级、prefetch(调用前注入)与 sync(回合后写回)生命周期、builtin 默认实现要求。
- `agent-memory-tools`:Agent 可调用记忆工具面——8 个工具的确切签名与语义、L1 编辑的精确匹配与歧义报错、revision 乐观并发、`memory_edit_log` 审计、弱检索升级门控 HintBlock、工具注册与 flag gating、workspace 隔离。
- `conversation-recall`:会话召回存储——写时索引、namespace 作用域与多租户隔离、`conversation_search` 检索语义(游标/时间窗/角色过滤/迭代排除)、与 event retention 的互不干涉、召回层自身保留策略。

### Modified Capabilities

(无——既有 spec 的可观察行为不变。session-memory REQ-4 曾承诺的工具名从未实现,本变更新 capability 以超集交付其意图,原规格文本在 archive 阶段随规格同步刷新。)

## Impact

- **协议与组合根**:`src/hecate/core/composition/memory_provider.py`(协议扩展、tier 路由)。
- **hecate-memory 包**:`packages/hecate-memory/src/hecate_memory/`——provider 默认实现(tier 方法)、recall 索引器与后台 worker(复用 `rag/embedding.py` + `rag/qdrant_store.py`)、prefetch/sync 实现;`memory/knowledge_tools.py` 死代码收编。
- **工具层**:`src/hecate/tools/tool/builtin.py`(工具定义 + executor 分支)、`src/hecate/tools/tool/registry.py`(seeding)。
- **数据模型与迁移**:`src/hecate/models/memory.py`(revision 列)+ 新增 `recall_messages`、`memory_edit_log` 模型;alembic 迁移(additive)。
- **运行时**:`src/hecate/runtime/context_processors.py` / `context_policy.py`(memory prefetch processor、升级门控 HintBlock,均排在 KV 保护前缀区之后、参与预算记账);回合提交处挂 recall 索引入队。
- **配置**:`src/hecate/core/config.py` 三个 feature flag(默认关)。
- **文档**:`docs/features/feature-catalog.md`(4.18/4.19/4.20 条目 + openjiuwen 勘误)、`docs/features/roadmap.md`、`docs/research/2026-09-memory-tools-survey.md` 及 parts 入库。
- **不涉及**:L2 压缩/ADR-033 compaction、REST API(`memory-api` 不变)、studio 前端(本期无 UI;Memory Center 类界面留待后续)。
