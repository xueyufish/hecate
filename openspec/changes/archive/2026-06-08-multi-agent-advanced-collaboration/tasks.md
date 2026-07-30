## 1. EventBus 核心（2.3a）

- [x] 1.1 创建 `engine/eventbus.py`，包含 `CollaborationEventType` 枚举（AGENT_MESSAGE、AGENT_REQUEST、AGENT_RESPONSE、TASK_ASSIGNED、TASK_COMPLETED、NEGOTIATION_PROPOSAL、NEGOTIATION_ACCEPT、NEGOTIATION_REJECT、DEBATE_ARGUMENT、DEBATE_REBUTTAL、DEBATE_CONCLUSION）
- [x] 1.2 向 `engine/eventbus.py` 添加 `CollaborationEvent` 冻结 dataclass（id、topic、sender、event_type、payload、timestamp）
- [x] 1.3 定义 `EventBus` ABC，包含抽象方法：`publish(topic, event)`、`subscribe(topic, handler)`、`unsubscribe(topic, handler)`、`close()`
- [x] 1.4 使用 `asyncio.Queue` 实现 `InMemoryEventBus`，每个主题独立队列、支持多个订阅者、以及关闭时刷新
- [x] 1.5 向 `PregelRuntime.__init__()` 添加 `event_bus: EventBus | None = None` 参数，并通过 `execution_context` 传递

## 2. EventBus 测试

- [x] 2.1 测试 `CollaborationEvent` 创建、不可变性、自动生成字段
- [x] 2.2 测试 `CollaborationEventType` 枚举值
- [x] 2.3 测试 `InMemoryEventBus` 的发布/订阅/取消订阅、主题隔离、多个订阅者、关闭刷新、空主题
- [x] 2.4 测试 `EventBus` ABC 不可实例化
- [x] 2.5 测试 PregelRuntime 在配置 event_bus 时在 execution_context 中传递它，未配置时省略

## 3. 协商模板（2.3b）

- [x] 3.1 实现 `build_negotiation_graph(proposer_model, responder_model, proposer_prompt, responder_prompt, max_rounds)`，返回包含 proposer AGENT 节点、responder AGENT 节点、check_agreement CONDITION 节点、协商循环边和 `agreement_status` LAST_VALUE 通道的 `GraphConfig`
- [x] 3.2 实现 `build_debate_graph(debater_a_model, debater_b_model, judge_model, rounds)`，返回包含交替辩论轮次、轮次计数器、可选的法官评估节点的 `GraphConfig`

## 4. 协商模板测试

- [x] 4.1 测试 `build_negotiation_graph` 生成有效的 `GraphConfig`，包含正确的节点、边、通道和入口点
- [x] 4.2 测试 `build_negotiation_graph` 输出通过 `GraphCompiler.compile()` 成功编译
- [x] 4.3 测试 `build_debate_graph` 在有和无法官的情况下生成有效的 `GraphConfig`
- [x] 4.4 测试 `build_debate_graph` 输出通过 `GraphCompiler.compile()` 成功编译
- [x] 4.5 测试工厂函数可从 `engine.templates` 导入

## 5. TaskAllocator（2.3c）

- [x] 5.1 创建 `engine/task_allocator.py`，包含定义 `allocate(task, candidates, create_if_not_found=False)` 返回 `AgentModel | None` 的 `TaskAllocator` ABC
- [x] 5.2 实现 `SemanticTaskAllocator`，调用 `port.llm_invoke()` 使用排名提示，解析 LLM 响应以找到最佳匹配，返回最佳候选；LLM 失败时记录错误并返回 None
- [x] 5.3 实现 `RoundRobinTaskAllocator`，顺序循环遍历候选；空候选时返回 None
- [x] 5.4 `create_if_not_found=True` 应引发 `NotImplementedError`，附带 P3 预留消息

## 6. TaskAllocator 测试

- [x] 6.1 测试 `TaskAllocator` ABC 不可实例化
- [x] 6.2 测试 `SemanticTaskAllocator` 调用 `port.llm_invoke()` 并返回最佳匹配智能体
- [x] 6.3 测试 `SemanticTaskAllocator` 在没有合适候选时返回 None
- [x] 6.4 测试 `SemanticTaskAllocator` 在 LLM 失败时返回 None（而非引发异常）
- [x] 6.5 测试 `RoundRobinTaskAllocator` 按顺序循环遍历候选
- [x] 6.6 测试 `RoundRobinTaskAllocator` 在空候选时返回 None
- [x] 6.7 测试 `create_if_not_found=True` 引发 `NotImplementedError`

## 7. Agent-as-Tool（2.3d）

- [x] 7.1 创建 `engine/agent_tool.py`，包含 `AgentDefinition` dataclass（agent_id、description、prompt_override、tools、disallowed_tools=["agent_execute"]、skills、model_override、context_mode="inherited"、max_turns、timeout_seconds）
- [x] 7.2 实现 `AgentTool` 类，包含 `name`、`description` 和 `execute(args)` 方法，调用 `port.agent_execute()` 并传入 AgentDefinition 的覆盖
- [x] 7.3 实现工具白名单/黑名单解析：如果 tools 不为 None → 白名单减去 disallowed；如果 tools 为 None → 继承全部减去 disallowed
- [x] 7.4 实现 `context_mode="isolated"`——AgentTool 仅使用任务创建新消息上下文；`"inherited"`——传递父级的消息通道
- [x] 7.5 通过 `asyncio.wait_for` 实现 `timeout_seconds` 强制执行
- [x] 7.6 通过 `agent_execute()` 的上下文参数实现 `max_turns` 强制执行

## 8. Agent-as-Tool 测试

- [x] 8.1 测试 `AgentDefinition` 最小创建（默认值：tools=None、disallowed_tools=["agent_execute"]、context_mode="inherited"）
- [x] 8.2 测试 `AgentDefinition` 完整创建包含所有字段
- [x] 8.3 测试 `AgentTool` 名称和描述从 AgentDefinition 派生
- [x] 8.4 测试 `AgentTool.execute()` 使用正确的参数调用 `port.agent_execute()`
- [x] 8.5 测试白名单减去黑名单解析（tools=["a","b","agent_execute"]、disallowed=["agent_execute"] → ["a","b"]）
- [x] 8.6 测试继承减去黑名单解析（tools=None、父级 tools=["a","b","agent_execute"]、disallowed=["agent_execute"] → ["a","b"]）
- [x] 8.7 测试空白名单（tools=[] → 无工具）
- [x] 8.8 测试 context_mode="isolated" 仅发送任务消息
- [x] 8.9 测试 context_mode="inherited" 发送父级消息
- [x] 8.10 测试 timeout_seconds 强制执行（超时时 TimeoutError）
- [x] 8.11 测试 max_turns 强制执行

## 9. 集成：EnginePort 扩展

- [x] 9.1 向 `EnginePort.agent_execute()` 默认实现添加 `agent_definition: AgentDefinition | None = None` 参数，无论如何引发 `NotImplementedError`
- [x] 9.2 更新 `AgentWorker` 以在节点配置中设置 `invocation_mode="tool"` 时传递 `agent_definition`

## 10. 集成测试

- [x] 10.1 测试 EventBus 与 PregelRuntime 的集成：智能体在图执行期间通过发布/订阅进行通信
- [x] 10.2 测试协商图成功执行 proposal → response → accept/reject 循环
- [x] 10.3 测试辩论图执行交替论点，带可选的法官裁决
- [x] 10.4 测试 TaskAllocator 与多智能体图中的智能体选择集成
- [x] 10.5 测试从 LLM 工具调用循环中调用 AgentTool

## 11. 验证

- [x] 11.1 运行 `ruff check src/hecate/ tests/`——0 错误
- [x] 11.2 运行 `ruff format --check src/ tests/`——0 错误
- [x] 11.3 运行 `mypy src/`——0 错误
- [x] 11.4 运行 `python -m pytest tests/ -q`——全部通过
