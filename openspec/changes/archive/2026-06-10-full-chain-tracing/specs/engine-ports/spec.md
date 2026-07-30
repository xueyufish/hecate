## MODIFIED Requirements — 修改的需求

### 需求：EnginePort 抽象接口解耦引擎与服务
`EnginePort` ABC 应定义 7 个抽象方法和 6 个可选方法，引擎为所有 I/O 调用这些方法，且不从服务层导入。2 个新的可选方法 `create_span` 和 `end_span` 使引擎层无需直接导入 OpenTelemetry 即可创建可观测性 span

#### 场景：LLM 调用
- **当** 调用 `llm_invoke(messages, config)`
- **则** 应返回 `AsyncGenerator[str, None]`，产出 token

#### 场景：工具执行通过 ToolRegistry 路由
- **当** 调用 `tool_execute(name, args, context)`
- **则** 应通过 ToolRegistry 路由调用，后者按名称和源类型解析工具，通过相应的执行器执行，并返回工具结果

#### 场景：通过 registry 执行工具
- **当** 调用 `tool_execute("web_search", {"query": "test"}, context)`
- **则** 适配器应委托给 `ToolRegistry.execute("web_search", {"query": "test"}, context)` 并返回 registry 的结果

#### 场景：工具未找到
- **当** 调用 `tool_execute("nonexistent", args, context)` 且工具不存在
- **则** 应抛出 `ValueError`，提示工具未找到

#### 场景：知识查询
- **当** 调用 `knowledge_query(query, kb_ids)`
- **则** 应返回包含内容和元数据的文档块字典列表

#### 场景：检查点保存
- **当** 调用 `checkpoint_save(state)`
- **则** 应持久化状态并返回 UUID 检查点 ID

#### 场景：为可观测性创建 span
- **当** 调用 `create_span(name="llm_call", attributes={"model": "gpt-4o"})`
- **则** 应返回包含 `span_id`、`trace_id` 和 `parent_id` 字段的 `SpanContext` 数据类，如果追踪已禁用则返回 `None`

#### 场景：创建带显式父级的 span
- **当** 调用 `create_span(name="tool_call", parent_id=<parent_span_id>, attributes={"tool": "search"})`
- **则** 应在指定父级下创建子 span 并返回其 `SpanContext`

#### 场景：使用输出和使用数据结束 span
- **当** 调用 `end_span(span_id=<id>, output_data={"result": "ok"}, usage={"input_tokens": 50})`
- **则** 应使用提供的输出和使用数据最终化 span

#### 场景：追踪禁用时创建 span
- **当** 调用 `create_span(name="llm_call")` 且没有追踪上下文存在
- **则** 应返回 `None` 而不抛出异常

#### 场景：结束不存在的 span
- **当** 调用 `end_span(span_id=<unknown_id>)`
- **则** 应返回而不抛出异常（空操作）
