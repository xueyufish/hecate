## 1. 数据模型与迁移

- [x] 1.1 在 `src/hecate/models/agent.py` 的 `AgentModel` 中添加 `opening_remarks`（TEXT 可为空）和 `enable_suggestions`（BOOLEAN 默认 true）列
- [x] 1.2 在 `AgentCreateSchema`、`AgentUpdateSchema`、`AgentReadSchema` 中添加 `opening_remarks` 和 `enable_suggestions` 字段
- [x] 1.3 为两个新列生成 Alembic 迁移
- [x] 1.4 在 `tests/test_models/test_agent.py` 中编写新的 Agent schema 字段测试

## 2. 建议类型和提示模板

- [x] 2.1 创建 `src/hecate/services/suggestions/types.py`，包含 `SuggestionResult` Pydantic schema（questions: list[str]、model: str、usage: dict）
- [x] 2.2 创建 `src/hecate/services/suggestions/prompts.py`，包含 `build_opening_prompt()` 和 `build_followup_prompt()` 函数
- [x] 2.3 在 `tests/test_services/test_suggestions/test_prompts.py` 中编写提示模板函数的测试

## 3. 建议服务

- [x] 3.1 创建 `src/hecate/services/suggestions/__init__.py`，包含模块文档字符串
- [x] 3.2 创建 `src/hecate/services/suggestions/service.py`，包含 `SuggestionService` 类，含 `generate_opening()` 和 `generate_suggestions()` 方法
- [x] 3.3 实现基于 LLM 的建议生成，2 秒超时并解析 JSON 数组
- [x] 3.4 实现静态回退：当 LLM 失败时从 Agent 角色设定中提取问题
- [x] 3.5 在 `tests/test_services/test_suggestions/test_service.py` 中编写 SuggestionService 测试

## 4. Conversation Service 集成

- [x] 4.1 为 `ConversationService.chat()` 方法签名添加 `generate_opening` 和 `generate_suggestions` 参数
- [x] 4.2 实现 `_generate_opening_remarks()` 方法，检查 Agent 配置并调用 SuggestionService
- [x] 4.3 实现 `_generate_followup_suggestions()` 方法，在回复后生成建议
- [x] 4.4 将开场白集成到 `_complete_chat()` — 返回带 suggested_questions 的问候语
- [x] 4.5 将开场白集成到 `_stream_chat()` — 输出内容后输出 suggestions 事件
- [x] 4.6 将后续建议集成到 `_complete_chat()` — 将 suggested_questions 追加到结果中
- [x] 4.7 将后续建议集成到 `_stream_chat()` — 在 done 之前输出 suggestions 事件

## 5. API 层

- [x] 5.1 向 `ChatCompletionRequest` 添加 `generate_opening`（bool 默认 false）和 `generate_suggestions`（bool 默认 false）
- [x] 5.2 向 `ChatMessage` 添加 `suggested_questions`（list[str] | None）字段
- [x] 5.3 更新 `create_chat_completion()`，在提供时将新标志传递给 ConversationService
- [x] 5.4 处理流式模式的开场白流程 — 输出问候内容，然后是 suggestions，最后是 done
- [x] 5.5 处理非流式模式的开场白流程 — 返回带 suggested_questions 的问候语
- [x] 5.6 在 `tests/test_api/test_opening_remarks.py` 中编写 API 集成测试

## 6. Context Assembler 增强

- [x] 6.1 为 `ContextAssembler.assemble()` 方法添加 `suggestion_mode` 参数
- [x] 6.2 实现开场建议模式 — 使用 Agent 元数据构建系统提示词
- [x] 6.3 实现后续建议模式 — 使用最近 2 轮对话构建系统提示词
- [x] 6.4 在 `tests/test_services/test_context/test_suggestion_mode.py` 中编写建议模式组装测试

## 7. 功能目录与验证

- [x] 7.1 更新功能目录 `docs/features/feature-catalog.md`，标记 1.3.8 为 ✅
- [x] 7.2 运行 `ruff check src/hecate/ tests/` 和 `ruff format --check src/ tests/` — 零错误
- [x] 7.3 运行 `mypy src/` — 无新增错误
- [x] 7.4 运行 `python -m pytest tests/ -q` — 所有测试通过
