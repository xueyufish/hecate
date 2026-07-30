## Context — 背景

引擎执行由 PregelRuntime 驱动，它在一个 BSP 循环中运行 LLMWorker。LLMWorker 处理 LLM 调用（`llm_invoke`）和工具执行（`tool_execute`）。目前没有在每次调用之前或之后运行的自定义逻辑挂钩点——安全拦截器仅在 API 层，在引擎的抽象内部不可见。

像 PII 屏蔽、成本限制、提示词注入检测和合规性检查这样的特性需要引擎级别的钩子，因为那是调用发生的地方。API 级别的检查在执行开始之前触发，不能逐调用应用。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 定义 4 个 hook ABC：`PreLLMHook`、`PostLLMHook`、`PreToolHook`、`PostToolHook`
- 为每个提供 `NoOp` 实现（默认——透传）
- 提供持有 hook 并在正确时间调用它们的 `GuardrailRegistry`
- 在每个 LLM 调用和工具执行周围将调用点集成到 LLMWorker 中
- 保持引擎零依赖

**非目标：**
- P3 的 AI 驱动的 guardrails（9.1a/9.1b）
- 跨 worker 的 guardrail 钩子（P3 的多代理编排）
- 基于事件的 guardrail 日志记录（P3 的 EventStore 集成）
- 生命周期钩子（在引擎启动/停止时运行）——只做每次调用

## Decisions — 设计决策

### D1：4 个独立的 hook，不是 1 个通用的

**选择**：4 个独立的 ABC（PreLLMHook、PostLLMHook、PreToolHook、PostToolHook），而不是一个带有枚举方法的通用 GuardrailHook。

**理由**：
- **清晰**：每个 hook 有单一的职责和明确的签名。
- **类型安全**：`on_pre_llm_call` 接收 LLMInvocation 并返回 LLMInvocation；`on_post_tool_call` 接收 ToolResult 并返回 ToolResult。它们不会被混淆。
- **组合**：一个 GuardrailRegistry 可以持有多个 hook（每个类型 0 个或多个），并且每个类型被独立调用。这与 Guardrails 的通用规范（pre/post 的独立规范）匹配得更好。

### D2：Hook 在 LLMWorker 内部，不在 PregelRuntime

**选择**：在 `src/hecate/engine/worker.py` 的 LLMWorker 中添加调用点，而不是在 PregelRuntime superstep 循环中。

**理由**：PregelRuntime 调用 Worker.execute() 并获得结果。它不直接调用 LLM 或工具。添加钩子的正确位置是 LLMWorker，它在生命周期中拥有实际调用点。这保持了 PregelRuntime 免于 worker 内部细节的干扰。

### D3：Hook 签名——可变对象，不是返回值

**选择**：每个 hook 方法接收并原地修改可变对象。例如，`on_pre_llm_call(self, invocation: LLMInvocation)` 修改 invocation 对象，而不是返回一个新的对象。

**理由**：
- **效率**：避免在 hook 链中复制 invocation/tool 结果。
- **链式处理**：一个 hook 修改对象，下一个 hook 看到修改后的版本。这允许自然的管道化（sanitizer → tracer → auditor）。
- **与 FastAPI 中间件模式和常见 guardrail 实现一致**。

### D4：GuardrailRegistry 持有每个类型的列表

**选择**：`GuardrailRegistry` 有 4 个列表：`pre_llm_hooks: list[PreLLMHook]`、`post_llm_hooks: list[PostLLMHook]` 等。每个列表可以包含 0 个或多个 hook。

**理由**：这允许多个独立 hook 共存。例如，一个用于 PII 屏蔽，一个用于成本跟踪，一个用于审核日志——都由不同的团队编写，注册在同一个 registry 中。

### D5：GuardrailRegistry 是 LLMWorker 的可选参数

**选择**：`LLMWorker.__init__` 接受一个可选的 `guardrails: GuardrailRegistry | None = None` 参数。

**理由**：与现有系统无缝集成。如果未提供 guardrails，则不调用任何 hook，行为与当前完全相同。

### D6：ONLY pre hooks 可以阻止执行

**选择**：pre hooks 可以通过设置 `invocation.blocked = True` 来阻止执行。Post hooks 不能——它们是只读的（用于审计/监控）。

**理由**：pre hooks 是策略强制执行点（安全、合规、成本）。Post hooks 是可观测性点。阻止已经发生的调用没有意义。

## Risks / Trade-offs — 风险与权衡

| 风险 | 缓解措施 |
|------|---------|
| hook 增加每次调用的延迟 | NoOp hook 是无操作的（基本上是免费的）。昂贵 hook 是可选加入的 |
| hook 可能阻止合法流量 | hook 默认是 NoOp；阻止必须通过注册 guardrail 显式加入 |
| hook 可能意外修改调用 | 文档约定：pre hooks 修改以清理；post hooks 不应该修改，但这不是强制执行的