# Tasks

## 1. Provider 契约与组合根(D1)

- [x] 1.1 扩展 `MemoryProvider` Protocol 与结构化结果类型(`MemoryFactHit`/`RecallPage`/`MemoryWriteResult`/`PrefetchEntry`),新增 `capabilities()` 声明;`search()` 签名保持兼容,现有测试全绿(验证:`python -m pytest tests/ -k memory_provider -q`)
- [x] 1.2 组合根按 `capabilities()` 路由:未声明能力返回结构化错误对象;provider 缺失/工厂失败降级语义不变并补齐用例(验证:单测覆盖三态——完整/部分能力/无后端)
- [x] 1.3 hecate-memory builtin provider 实现完整契约:tier-2/3 方法委托既有 `WorkingMemoryService`/`UserMemoryService`/`KnowledgeMemoryService`(`add` 复用 L4 规范化去重),生命周期钩子先落地接口与默认空实现(验证:builtin provider 契约符合性单测——逐能力断言)
- [x] 1.4 三个 feature flag 接入 `core/config.py`(`MEMORY_TOOLS_ENABLED`/`RECALL_INDEXING_ENABLED`/`MEMORY_PREFETCH_ENABLED`,默认 False)及 memory 段阈值/限额配置(验证:settings 单测 + 关闭态启动 smoke)

## 2. 数据模型与迁移(D4/D5)

- [x] 2.1 `MemoryBlockModel`/`MemoryModel`/`KnowledgeMemoryModel` 增加 `revision` 列,alembic 迁移(server_default "1",additive)(验证:`alembic upgrade head` 于干净库与带数据库均可执行,downgrade 可回退)
- [x] 2.2 新增 `RecallMessageModel`(`recall_messages`,唯一约束 `session_id+content_hash+seq`,索引:workspace/agent/conversation)与 `MemoryEditLogModel`(`memory_edit_log`)及迁移(验证:迁移可逆性测试)
- [x] 2.3 三个记忆表的写路径统一递增 revision:service 层 update/forget 与 REST 写路径均 `revision+1`(验证:service 层单测断言 revision 单调)

## 3. 记忆工具层(D2/D3/D9)

- [x] 3.1 `BUILTIN_TOOL_DEFINITIONS` 新增 8 个工具 JSON Schema(`memory_replace`/`memory_insert`/`memory_rethink`/`memory_search`/`memory_add`/`memory_update`/`memory_forget`/`conversation_search`),risk_level 按读 LOW/写 MEDIUM;seeding 集合受 `MEMORY_TOOLS_ENABLED` 约束(验证:seeding 单测——flag 关零新增、flag 开 8 个入库)
- [x] 3.2 `BuiltInToolExecutor` 注入可选 `memory_backend`;身份参数(`agent_id`/`workspace_id`/`session_id`)缺失时 fail-closed 结构化报错;删除 `knowledge_tools.py` 死代码并确认 MCP server 管理面工具不受影响(验证:executor 单测 + `python -m pytest tests/ -k mcp_server -q` 回归)
- [x] 3.3 L1 编辑工具实现:精确匹配/歧义拒绝/空串删除/超限显式报错/label 不存在报错,成功返回编辑后内容与 revision(验证:逐语义单测,含跨 Agent 不可见场景)
- [x] 3.4 L3/L4 工具实现:`memory_search`(合并检索 + 标注来源层 + access_count 递增)、`memory_add`(规范化去重)、`memory_update`/`memory_forget`(revision 乐观并发,冲突错误含当前 revision)(验证:单测覆盖跨层检索、去重、并发冲突三场景)
- [x] 3.5 `memory_edit_log` 审计写入:所有工具路径的 L1/L3/L4 变更落审计(before/after 摘要 ≤200 chars、trace 关联);审计无任何修改/删除工具面(验证:单测断言每类变更产生审计行)
- [x] 3.6 工具经既有 tool policy pipeline 与 guardrail hooks 执行的回归:记忆工具调用产生 TOOL_CALL/TOOL_RESULT 事件且被 policy 正常拦截(验证:policy 拦截用例 + 事件断言)

## 4. 会话召回(D5/D6)

- [x] 4.1 `RecallIndexerService`:TURN_END 回调通道(组合根 flag 门控注册)+ 水位轮询兜底(间隔可配);幂等(唯一约束吸收重复);embedding 失败不阻塞、可追平(验证:集成测试——索引/重复/失败追平三场景,对话路径零阻塞)
- [x] 4.2 Qdrant `hecate_recall` 集合:懒创建、payload 含作用域字段、检索按 workspace/agent filter;conversation 删除级联清理(向量 + 元数据)(验证:qdrant mock 测试覆盖 filter 与级联)
- [x] 4.3 `conversation_search` 执行面:query/limit/start_date/end_date/roles/cursor/exclude_session_ids 全参数;游标 = `(score, id)` 不透明续读;低信号判定(空结果或 top1 < 阈值)返回结构化空结果标记(验证:参数矩阵单测 + 游标翻页无重复断言)
- [x] 4.4 与 event retention 互不干涉验证:retention 清理事件后召回可检索;`RECALL_INDEXING_ENABLED=false` 时零写入(验证:retention + 召回联合测试)
- [x] 4.5 可观测面:按 workspace/agent 的条目计数、最近索引时间、待补索引队列深度的查询接口(内部 API/CLI 级即可)(验证:接口测试)

## 5. prefetch 与升级门控(D7/D8)

- [x] 5.1 `MemoryPrefetchProcessor`(run_mode=always,链尾追加):最近 2 轮投影文本为查询,`<memory>` 块尾部注入,`max_entries`/`max_tokens` 上限,`token_capped` 时跳过,prefetch 失败降级跳过(验证:processor 单测——注入/超限/降级/跳过四场景 + 注入位置在 KV 前缀区之后的断言)
- [x] 5.2 `RetrievalEscalationHintProcessor`:消费 `execution_context` 低信号标记,单轮一次提示 + per-session 冷却;工具循环在弱结果时写入标记(验证:processor 单测 + 防抖/冷却用例)
- [x] 5.3 预算协同回归:prefetch 块计入预算快照、可被预算处理器裁剪;`MEMORY_PREFETCH_ENABLED=false` 时链行为与现状一致(验证:`python -m pytest tests/test_runtime -k context_processor -q` 全绿 + 新增协同用例)

## 6. 文档、勘误与收尾

- [x] 6.1 调研文档入库:`docs/research/2026-09-memory-tools-survey.md` + `2026-09-memory-tools-survey.parts/`(6 份,按 docs 写作规范复核)(验证:文件存在且链接可达)
- [x] 6.2 feature-catalog 更新:4.16/4.18/4.19/4.20 条目(4.20 改写为 Retrieval Escalation,reference 换 OpenClaw Active Memory);openjiuwen 归属勘误(华为系,`jd-opensource` 404 → `openJiuwen-ai`);roadmap 对应行同步(验证:`grep -n "4\.1[89]\|4\.20" docs/features/feature-catalog.md` 输出与变更一致)
- [x] 6.3 文档补齐:`docs/design/knowledge-memory-design.md` 记忆管理表增补工具层小节;`docs/gotchas.md` 增补身份参数必传与 flag 语义(验证:文档链接与术语走查)
- [x] 6.4 全量验证:`ruff check src/hecate/ packages tests/`、`ruff format --check src/ packages tests/`、`mypy src/`、`python -m pytest tests/ -q` 四项全绿;flags 默认关闭下启动 smoke 与关键回归(验证:四项命令输出 0 error / 全部通过)
