## 1. 基础设施搭建

- [x] 1.1 创建 `src/hecate/services/context/` 模块及 `__init__.py`
- [x] 1.2 在 `pyproject.toml` 中添加 `tiktoken` 依赖
- [x] 1.3 在 `models/evidence.py` 中创建 `EvidenceModel` ORM 模型（evidences 表：id, session_id, conversation_id, message_id, tool_name, tool_arguments JSONB, raw_content TEXT, normalized_content JSONB, is_error BOOL, importance FLOAT, source_type VARCHAR, provenance JSONB, created_at）
- [x] 1.4 在 `models/budget.py` 中创建 `BudgetSnapshotModel` ORM 模型（budget_snapshots 表：id, session_id, total_budget INT, tokens_used INT, tokens_remaining INT, degradation_level VARCHAR, created_at）
- [x] 1.5 创建 Evidence 和 BudgetSnapshot 的 Pydantic schema（CreateSchema, ReadSchema）
- [x] 1.6 为新表生成 Alembic 迁移

## 2. 预算治理

- [x] 2.1 在 `services/context/token_counter.py` 中实现 `TokenCounter` 类——封装 tiktoken，提供 `count_messages()` 方法接受消息字典列表，返回总 token 数
- [x] 2.2 在 `services/context/budget.py` 中实现 `BudgetManager` 类——跟踪每个 session 的预算分配和累积使用量；`check_budget(session_id, messages) -> BudgetCheck` 返回预算状态和赤字
- [x] 2.3 实现 Level 1 降级（DROP）——从消息列表中过滤掉优先级为"low"的消息
- [x] 2.4 实现 Level 2 降级（COMPRESS）——使用 LLM 将中等优先级消息压缩为简短摘要段落
- [x] 2.5 实现 Level 3 降级（EMERGENCY）——用包含以下内容的单一紧急摘要替换整个历史：原始目标、关键决策、当前状态
- [x] 2.6 实现 `degrade(messages, deficit, priorities) -> list[dict]` 编排器，按顺序应用降级级别直到符合预算
- [x] 2.7 实现预算快照记录——每次 LLM 调用后，持久化 BudgetSnapshotModel 及其使用数据

## 3. 上下文组装器

- [x] 3.1 在 `services/context/types.py` 中实现 `AssembledContext` 数据类——包含 messages, tools, metadata（phase, token_count, priorities）
- [x] 3.2 在 `services/context/prioritizer.py` 中实现消息优先级分配——根据角色、时间和内容类型分配 critical/high/medium/low
- [x] 3.3 在 `services/context/phase_detector.py` 中实现任务阶段检测——将近期消息模式分类为 explore/converge/execute/verify
- [x] 3.4 在 `services/context/tool_filter.py` 中实现按阶段过滤工具——根据检测到的阶段和 agent 的阶段-工具映射过滤工具列表
- [x] 3.5 在 `services/context/work_panel.py` 中实现任务工作面板构建——对于超过 3 轮的对话，构建结构化面板：目标 + 近期交流 + 最新工具结果 + 旧消息摘要
- [x] 3.6 实现 `ContextAssembler.assemble(messages, tools, knowledge, session_meta) -> AssembledContext`——编排 prioritizer → phase_detector → tool_filter → work_panel → budget_check

## 4. 证据管理

- [x] 4.1 实现 `EvidenceTracker.capture(tool_name, args, result, context) -> EvidenceRecord`——拦截工具结果并创建结构化证据记录
- [x] 4.2 实现证据归一化——解析 JSON 输出、包装纯文本、处理错误结果
- [x] 4.3 实现来源追踪——在每个证据记录中填充 session_id, conversation_id, message_id, turn_index
- [x] 4.4 实现重要性评分——默认 0.5，错误=0.0，被引用时 +0.1（上限 1.0）
- [x] 4.5 实现证据持久化——通过异步 session 将 EvidenceModel 保存到数据库
- [x] 4.6 实现证据查询接口——`query(session_id, tool_name, min_importance, time_range) -> list[EvidenceRecord]`

## 5. Provider 适配

- [x] 5.1 在 `services/context/provider_shaping.py` 中实现 `ProviderStrategy` ABC——抽象 `shape(context: AssembledContext) -> AssembledContext`
- [x] 5.2 实现 `DefaultStrategy`——直接透传，不做修改
- [x] 5.3 实现 `OpenAIStrategy`——截断超过 2000 token 的 system 消息，在 messages 数组中保留 system 消息
- [x] 5.4 实现 `AnthropicStrategy`——将 system 消息提取为顶级参数，适配工具定义格式
- [x] 5.5 实现策略注册和自动选择——基于模型名称前缀的 `get_strategy(model: str) -> ProviderStrategy`，提供自定义策略的注册 API

## 6. 集成

- [x] 6.1 向 `EnginePort` ABC 添加 `context_assemble` 和 `evidence_query` 方法（带默认空实现以保持向后兼容）
- [x] 6.2 修改 `ConversationService._complete_chat()` 以在 `llm_service.chat()` 之前调用 `ContextAssembler.assemble()`，并用 `EvidenceTracker.capture()` 包装工具执行
- [x] 6.3 修改 `ConversationService._stream_chat()` 以在流式处理之前调用 `ContextAssembler.assemble()`，并用 `EvidenceTracker.capture()` 包装工具执行
- [x] 6.4 在传递给 `LLMService` 之前对组装后的上下文应用 `ProviderStrategy.shape()`
- [x] 6.5 将 `BudgetManager` 接入组装管道——组装后检查预算，必要时应用降级

## 7. 测试

- [x] 7.1 `TokenCounter` 单元测试——验证已知消息集的 token 计数准确性
- [x] 7.2 `BudgetManager` 单元测试——预算分配、检查、三级降级（使用 mock 消息）
- [x] 7.3 `ContextAssembler` 单元测试——透传模式、阶段检测、工具过滤、工作面板构建
- [x] 7.4 `EvidenceTracker` 单元测试——捕获、归一化、来源追踪、重要性评分、查询
- [x] 7.5 Provider 策略单元测试——OpenAI 截断、Anthropic system 消息提取、Default 透传
- [x] 7.6 完整管道集成测试：消息 → 组装器 → 预算检查 → Provider 适配 → LLM mock
- [x] 7.7 带证据捕获和预算更新的工具执行集成测试
