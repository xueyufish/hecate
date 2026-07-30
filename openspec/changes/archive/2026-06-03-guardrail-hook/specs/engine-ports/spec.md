## MODIFIED Requirements — 修改后的需求

### Requirement: Optional event_store property for Event Persistence — 用于事件持久化的可选 event_store 属性
The EnginePort SHALL expose an optional `event_store` property that returns an EventStore instance or None.

EnginePort 应暴露一个可选的 `event_store` 属性，返回 EventStore 实例或 None。

#### Scenario: Event store not configured — 未配置事件存储
- **WHEN** a concrete EnginePort does not set `event_store`
- **THEN** the property SHALL return `None`
- **当**具体的 EnginePort 未设置 `event_store`
- **则**该属性应返回 `None`

#### Scenario: Event store configured — 已配置事件存储
- **WHEN** a concrete EnginePort sets `event_store` to an EventStore instance
- **THEN** `port.event_store` SHALL return that instance
- **当**具体的 EnginePort 将 `event_store` 设置为 EventStore 实例
- **则** `port.event_store` 应返回该实例

### Requirement: Optional guardrail hook properties for Guardrail Integration — 用于护栏集成的可选护栏钩子属性
The EnginePort SHALL expose four optional properties for guardrail hooks: `pre_llm_hooks` (returns `list[PreLLMHook]`), `post_llm_hooks` (returns `list[PostLLMHook]`), `pre_tool_hooks` (returns `list[PreToolHook]`), `post_tool_hooks` (returns `list[PostToolHook]`). Each default implementation SHALL return an empty list.

EnginePort 应暴露四个可选的护栏钩子属性：`pre_llm_hooks`（返回 `list[PreLLMHook]`）、`post_llm_hooks`（返回 `list[PostLLMHook]`）、`pre_tool_hooks`（返回 `list[PreToolHook]`）、`post_tool_hooks`（返回 `list[PostToolHook]`）。每个默认实现应返回空列表。

#### Scenario: No guardrail hooks configured — 未配置护栏钩子
- **WHEN** a concrete EnginePort does not override any guardrail property
- **THEN** all four properties SHALL return `[]`
- **当**具体的 EnginePort 未覆盖任何护栏属性
- **则**所有四个属性应返回 `[]`

#### Scenario: Pre-LLM hooks configured — 已配置 Pre-LLM 钩子
- **WHEN** a concrete EnginePort overrides `pre_llm_hooks` to return `[hook1, hook2]`
- **THEN** `port.pre_llm_hooks` SHALL return that list
- **当**具体的 EnginePort 覆盖 `pre_llm_hooks` 返回 `[hook1, hook2]`
- **则** `port.pre_llm_hooks` 应返回该列表
