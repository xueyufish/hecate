## Context

Hecate P1 完成了执行引擎、API 层、LLM 服务、RAG 管道、安全层、MCP 集成等核心功能。当前 LLM 调用链路为：

```
ConversationService.chat(messages, model, tools)
  → LLMService.chat(messages, model, tools)
    → litellm.acompletion(messages, model, tools)
```

消息是原样透传的——对话历史、工具定义直接拼接到 prompt。这意味着：

1. **无预算管理**：长对话或工具密集场景容易超出上下文窗口，P1 无任何 token 计数或降级策略
2. **无上下文优先级**：所有消息平等对待，早期不重要的对话和历史关键决策占据同等 token
3. **工具结果无结构化**：`_execute_tools()` 返回原始字符串，无来源追踪、无归一化
4. **无 Provider 差异适配**：LiteLLM 统一了 API 调用，但不同 Provider 对 system message 长度、工具定义格式、指令风格有不同偏好

**约束**：
- 零破坏性变更——P1 API 接口、数据模型、引擎行为保持不变
- 新模块通过依赖注入集成，不修改 P1 核心代码路径
- EnginePort 是引擎与服务层的唯一边界，新增能力通过 Port 扩展

## Goals / Non-Goals

**Goals:**

1. 在每次 LLM 调用前，根据任务阶段动态组装上下文（消息子集 + 工具列表 + 知识片段）
2. 按 session 维度管理 Token 预算，超出时执行结构化降级
3. 对工具调用结果进行归一化处理和来源追踪
4. 根据目标 LLM Provider 特性对最终上下文进行格式适配

**Non-Goals:**

1. 不实现完整的记忆系统（L1 工作记忆 / L3 用户记忆）——属于 P2 独立变更
2. 不实现 L2 会话压缩管道（snip→microcompact→autocompact）——属于 P2 记忆增强
3. 不实现前端 UI 展示（上下文可视化、预算看板）——属于 P2 前端变更
4. 不修改 Graph DSL 或编译器——Context Engineering 是服务层能力，不影响编排层

## Decisions

### D1: Context Assembler 作为 LLM 调用前的中间层

**选择**：在 `ConversationService` 和 `LLMService` 之间插入 `ContextAssembler`

```
P1: ConversationService → LLMService → LiteLLM
P2: ConversationService → ContextAssembler → LLMService → LiteLLM
```

**理由**：
- 最小侵入——只需修改 `ConversationService` 的调用链路，LLMService 本身不变
- 可退化为 pass-through——当 Context Engineering 未启用时，`ContextAssembler.assemble()` 直接返回原始消息
- 符合五层架构——Context Assembler 属于能力服务层，不侵入编排层和引擎层

**备选方案**：
- ❌ 在 Engine Port 层做：Port 是引擎的边界接口，上下文组装是服务层关注点，放错层
- ❌ 在 Pregel 节点内部做：引擎层不应关心上下文组装细节，违反分层原则

### D2: Token 计数使用 tiktoken（cl100k_base）

**选择**：使用 `tiktoken` 库进行 token 计数，默认使用 `cl100k_base` 编码

**理由**：
- cl100k_base 覆盖 GPT-4/4o/3.5-turbo 系列，是使用最广泛的编码
- tiktoken 是 OpenAI 官方库，轻量、快速（Rust 实现）
- 对于非 OpenAI 模型（Claude/Qwen），token 计数是近似值，但足以做预算管理

**备选方案**：
- ❌ LiteLLM 内置 `cost_per_token()`：只能事后统计，不能事前预算
- ❌ 自建 tokenizer：维护成本高，准确性不如 tiktoken

### D3: 预算降级采用三级策略

**选择**：Token 预算超出时，按优先级依次执行：

```
Level 1: DROP — 丢弃低优先级消息（系统通知、早期闲聊）
Level 2: COMPRESS — 压缩中等优先级消息（历史对话 → 摘要）
Level 3: EMERGENCY — 紧急摘要（全部历史 → 一段摘要）
```

**理由**：
- 渐进式降级——优先保留最重要的上下文，逐步放弃低价值信息
- 可配置——每级阈值可通过 Agent 配置自定义
- 与 L2 压缩管道兼容——Level 2/3 的压缩能力在 P2 后期可替换为完整压缩管道

### D4: 证据存储使用独立表，不嵌入 Message

**选择**：新增 `evidences` 表，通过 `message_id` 关联到 `messages` 表

**理由**：
- 关注点分离——证据是结构化数据（来源、置信度、归一化内容），与消息文本不同
- 独立查询——证据可以按工具类型、时间范围、重要性独立检索
- 不影响现有 Message model——零破坏性变更

### D5: Provider Shaping 使用策略模式

**选择**：为每个 Provider family 定义一个 `ProviderStrategy`，在 Context Assembler 输出后应用

```python
class ProviderStrategy(ABC):
    @abstractmethod
    def shape(self, context: AssembledContext) -> AssembledContext: ...

class OpenAIStrategy(ProviderStrategy): ...
class AnthropicStrategy(ProviderStrategy): ...
class DefaultStrategy(ProviderStrategy): ...
```

**理由**：
- 开放扩展——新增 Provider 只需添加 Strategy 类
- 最小默认——P2 只实现 OpenAI 和 Anthropic 两种策略，其他走 Default
- 策略选择由模型名称自动推断（`gpt-*` → OpenAI, `claude-*` → Anthropic）

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| tiktoken token 计数对非 OpenAI 模型不准确 | 接受近似值——预算管理不需要精确到 token，±10% 误差可接受；可在 Strategy 中为特定 Provider 添加校正系数 |
| Context Assembler 增加调用延迟 | 组装是纯内存操作，预期 < 5ms；预算计算和降级是轻量级计算；仅在超出预算时触发压缩（有延迟开销） |
| 证据表增长快 | 设置 TTL 清理策略（默认 30 天）；证据大小限制（单条 < 10KB）；重要证据可手动标记永久保留 |
| Provider Shaping 维护成本 | P2 只实现 2 种策略；默认策略处理所有未知 Provider；策略逻辑保持简单（格式调整，不做内容改写） |
| 与 P2 后期 L2 压缩管道重叠 | Budget Manager 的 Level 2/3 降级是简化版压缩，后续可替换为完整压缩管道的调用，接口兼容 |
