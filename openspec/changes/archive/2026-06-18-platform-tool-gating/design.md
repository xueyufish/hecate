## Context — 背景

Hecate 的 LLMWorker 通过 `node_config.get("tools")` → `context_assemble` → `llm_invoke` 在每次调用时将完整的工具列表传递给 LLM。没有机制可以根据运行时状态有条件地隐藏工具。这导致当 Agent 拥有许多工具时上下文膨胀，并阻止业务规则执行（例如，"管理员工具仅对管理员可见"）。

10 平台研究发现：
- **Salesforce Agentforce** 使用带自定义 DSL（`@variables.verified == True`）的 `available when`——软门控，每次调用，平台评估
- **Google ADK** 使用 `ToolPredicate`（Python 可调用对象）——软门控，每次调用
- **Alibaba AgentScope** 使用工具组 + 元工具——LLM 自行管理组激活
- **OpenClaw** 使用多层策略系统（profile → allow/deny → provider → sandbox）
- **所有平台仅使用软门控**——无执行层硬阻断
- **Dify** 有一个开放的 issue #27887 请求此确切功能

当前代码库注入点：`LLMWorker.execute()` 第 191 行（`tools = node_config.get("tools")`）和 `execute_stream()` 第 330 行。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 向 ToolModel 添加 `available_when` 字段，用于声明性工具可见性条件
- 在 LLM 调用前每次调用评估条件（软门控）
- 支持 Python 安全的表达式语言，带运行时上下文变量
- 零新外部依赖（使用 Python 内置 `eval()` 带受限命名空间）
- 向后兼容——没有 `available_when` 的工具行为完全不变

**非目标：**
- 执行层硬门控（10 平台共识：不需要）
- LLM 自行管理工具组（AgentScope 模式——推迟到未来功能）
- 工具搜索/延迟加载（Claude 模式——正交关注点）
- 用于编辑 `available_when` 表达式的可视化画布 UI（未来增强）
- 现有工具的迁移（字段可为空，默认为 None = 始终可用）

## Decisions — 决策

### Decision 1: 表达式语言——带受限命名空间的 Python 安全 `eval()`

**选择**：使用 Python 内置的 `eval()`，带受限命名空间（`__builtins__: {}`，无 `__import__`）。

**理由**：
- Salesforce 使用自定义 DSL；Google ADK 使用 Python 可调用对象；IBM 使用自然语言
- Python 表达式对 Hecate 的开发人员群体来说立即可理解
- 无新依赖（不像 CEL 需要 `cel-python`）
- `eval()` 命名空间仅限于提供的上下文变量——没有内置函数，没有导入，无法访问危险对象的属性

**表达式示例**：
```python
# 简单相等
"user_role == 'admin'"

# 带 and/or 的复合
"phase == 'EXECUTE' and budget_remaining > 1000"

# 成员检查
"'delete' in user_permissions"

# 否定
"not user_role == 'guest'"
```

**考虑的替代方案**：
- **CEL（通用表达式语言）**：Google 的表达式语言。安全、规范，但增加了 `cel-python` 依赖。对于简单条件来说过于复杂。
- **JSON Logic**：结构化的 JSON 条件。安全但冗长且难以读写。示例：`{"and": [{"==": [{"var": "phase"}, "EXECUTE"]}, {">": [{"var": "budget_remaining"}, 1000]}]}`
- **ast.literal_eval**：限制太多——只支持字面量，不支持比较或布尔逻辑。

### Decision 2: 注入点——LLMWorker `_filter_tools()` 方法

**选择**：在 `LLMWorker` 中添加一个私有 `_filter_tools(tools, execution_context, channel_snapshot)` 方法，在 `tools = node_config.get("tools")` 之后和 `PreLLMHook` 之前调用。

```
LLMWorker.execute()
  ① tools = node_config.get("tools")              ← L191 提取
  ② tools = self._filter_tools(tools, ...)         ← 新：评估 available_when
  ③ PreLLMHook.on_pre_llm_call(tools=tools)        ← L196 hook 看到过滤后的列表
  ④ context_assemble(tools=tools)                  ← L224 塑造过滤后的列表
  ⑤ llm_invoke(tools=shaped_tools)                 ← L251 LLM 看到过滤后的列表
```

**理由**：
- 在 PreLLMHook 之前过滤意味着 hooks 看到已经过滤的列表（一致）
- 所有平台在 LLM 调用前过滤——这是最早合理的注入点
- 只有 LLMWorker 需要过滤器——ToolWorker 已经使用 PreToolHook 作为执行级守卫

**考虑的替代方案**：
- **扩展 PreLLMHook 以修改工具**：需要改变 GuardrailResult 以支持工具列表修改。为单个用例增加 hook 合同的复杂性。
- **新的 ToolGateHook ABC**：对于简单的过滤器来说过度设计。将是第 6 种 hook 类型。
- **在 ToolRegistry 中过滤**：太晚了——ToolRegistry 处理执行路由，而不是 LLM 可见性。工具需要在 LLM 看到它们之前被过滤。

### Decision 3: ToolGateEvaluator——独立评估器类

**选择**：在 `engine/tool_gate.py` 中将 `ToolGateEvaluator` 实现为独立类（不是 ABC）。

```python
class ToolGateEvaluator:
    """针对运行时上下文评估 available_when 表达式。"""

    def evaluate(self, expression: str, context: dict) -> bool:
        """评估单个 available_when 表达式。如果工具可用则返回 True。"""

    def filter_tools(
        self, tools: list[dict], context: dict
    ) -> list[dict]:
        """过滤工具列表，移除 available_when 评估为 False 的工具。"""
```

**理由**：
- 将表达式评估逻辑保持在一个地方（可测试、可重用）
- 不是 ABC，因为只有一种评估策略（带受限命名空间的 Python eval）
- 遵循引擎层零依赖规则
- 如果我们以后想要可插拔的评估器（CEL、JSON Logic），这个类可以变成 ABC

**考虑的替代方案**：
- **在 LLMWorker 中内联**：更简单但更难隔离测试，并在 `execute()` 和 `execute_stream()` 之间重复逻辑。
- **新的引擎 ABC**：过度设计。现阶段不需要可插拔性。如果需要，稍后可以提取 ABC。

### Decision 4: 上下文变量——来自 execution_context + channel_snapshot 的扁平字典

**选择**：通过合并以下内容构建扁平上下文字典：
- `execution_context` 键：`session_id`、`superstep`、`trace_id`
- `channel_snapshot` 键：`_user_id`、`_agent_id`、`_turn_index`
- 派生值：`phase`（来自任务阶段检测 4.9）、`budget_remaining`（来自 Token 预算 4.10）、`user_role`（来自 RBAC 上下文）

```python
context = {
    "session_id": "...",
    "superstep": 3,
    "user_id": "...",
    "user_role": "admin",        # 来自 RBAC
    "turn_index": 5,
    "phase": "EXECUTE",           # 来自任务阶段检测
    "budget_remaining": 8000,     # 来自 Token 预算
}
```

**理由**：
- 扁平命名空间对表达式作者来说最简单（`user_role == 'admin'` vs `context.user.role == 'admin'`）
- 不可用的变量（例如，没有 RBAC 上下文）只是不在字典中——引用它们的表达式抛出 NameError，被捕获并视为"工具不可用"（故障关闭）

**考虑的替代方案**：
- **嵌套上下文对象**（`context.user.role`）：更结构化但对表达式作者来说冗长，需要一个上下文对象类。
- **带前缀的变量**（`@variables.user_role` 像 Salesforce）：在 Python 原生系统中增加了 DSL 复杂性而没有好处。

### Decision 5: 评估错误时故障关闭

**选择**：如果 `available_when` 表达式抛出任何异常（NameError、SyntaxError、TypeError），该工具被视为**不可用**（被过滤掉）。

**理由**：
- 安全优先：如果我们不能确定工具是安全的，就隐藏它
- 防止错误驱动的工具暴露（格式错误的表达式意外显示敏感工具）
- 记录 WARNING 以便开发人员调试其表达式

**考虑的替代方案**：
- **故障开放**（出错时显示工具）：更宽松但对安全敏感的工具风险较大。一个拼写错误可能暴露管理工具。

## Risks / Trade-offs — 风险 / 权衡

| 风险 | 缓解措施 |
|------|------------|
| **`eval()` 安全**：恶意表达式可能尝试访问危险函数 | 受限命名空间（`__builtins__: {}`，无 `__import__`）。只有上下文变量在作用域中。表达式作者无论如何都是具有代码访问权限的开发人员。 |
| **性能**：每次调用对每个工具评估表达式会增加开销 | 表达式通常很短（< 100 字符）。对简单表达式的 `eval()` 是微秒级的。对于 20 个工具，总开销 < 1ms。相比 LLM 延迟可以忽略不计。 |
| **派生变量不可用**：`phase`、`budget_remaining`、`user_role` 可能并不总是填充 | 故障关闭：缺失变量导致 NameError → 工具隐藏。开发人员必须确保上下文在依赖它之前被填充。 |
| **表达式调试**：开发人员可能在表达式语法错误上遇到困难 | 评估失败时记录 WARNING，包含表达式文本和可用变量。未来：工具配置 UI 中的干运行验证器。 |
| **无硬门控**：LLM 理论上可能幻觉调用被门控的工具 | ToolWorker 中的 PreToolHook 作为执行级守卫。ToolRegistry.execute() 也检查工具是否存在。两层防御。 |
